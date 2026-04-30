import os
import requests
import asyncio
from datetime import datetime
from telegram import Bot

# Environment Variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
API_KEY = os.getenv("API_KEY")
CHAT_ID = os.getenv("CHAT_ID")

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {
    "x-rapidapi-host": "v3.football.api-sports.io",
    "x-apisports-key": API_KEY
}

# League Weightings (API-Football League IDs)
HIGH_DRAW_LEAGUES = {135: 0.04, 61: 0.04, 128: 0.05, 253: 0.03, 284: 0.06}
LOW_DRAW_LEAGUES = {78: -0.04, 88: -0.05}

def get_daily_data():
    """Fetches fixtures (for names) and odds, then matches them together."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    
    # STEP 1: Get the Fixtures Dictionary (To get the real team names)
    fixtures_endpoint = f"{BASE_URL}/fixtures?date={today}"
    fixtures_response = requests.get(fixtures_endpoint, headers=HEADERS)
    
    match_dictionary = {}
    if fixtures_response.status_code == 200:
        fixtures_data = fixtures_response.json().get('response', [])
        for f in fixtures_data:
            fixture_id = f['fixture']['id']
            match_dictionary[fixture_id] = {
                "home": f['teams']['home']['name'],
                "away": f['teams']['away']['name'],
                "league": f['league']['name']
            }

    # STEP 2: Get the Odds
    odds_endpoint = f"{BASE_URL}/odds?date={today}&bookmaker=1"
    odds_response = requests.get(odds_endpoint, headers=HEADERS)
    odds_data = []
    if odds_response.status_code == 200:
        odds_data = odds_response.json().get('response', [])
        
    return odds_data, match_dictionary

def calculate_probabilities(match_data, match_dictionary):
    """Calculates AI Probability, Market Probability, and Value Edge."""
    try:
        fixture = match_data['fixture']
        fixture_id = fixture['id']
        bookmakers = match_data['bookmakers']
        
        if not bookmakers:
            return None
            
        bets = bookmakers[0]['bets']
        
        # Extract Match Winner Odds (Id 1)
        match_winner_bet = next((b for b in bets if b['id'] == 1), None)
        if not match_winner_bet:
            return None
            
        home_odds = float(next(v['odd'] for v in match_winner_bet['values'] if v['value'] == 'Home'))
        draw_odds = float(next(v['odd'] for v in match_winner_bet['values'] if v['value'] == 'Draw'))
        away_odds = float(next(v['odd'] for v in match_winner_bet['values'] if v['value'] == 'Away'))

        # Extract Under 2.5 Goals Odds (Id 5)
        ou_bet = next((b for b in bets if b['id'] == 5), None)
        under_2_5_odds = 2.0 # Default baseline
        if ou_bet:
            try:
                under_2_5_odds = float(next(v['odd'] for v in ou_bet['values'] if v['value'] == 'Under 2.5'))
            except StopIteration:
                pass

        # 1. Market Probability
        market_prob = 1 / draw_odds

        # Look up the real names from our dictionary
        real_names = match_dictionary.get(fixture_id, {"home": f"Team {fixture_id}", "away": "Away", "league": "Unknown"})

        # 2. AI Probability Calculation
        base_prob = 0.28 
        
        # We need the league ID for our weightings. Sometimes it's in the odds data, sometimes not.
        league_id = match_data.get('league', {}).get('id', 0)
        league_weight = HIGH_DRAW_LEAGUES.get(league_id, 0.0) + LOW_DRAW_LEAGUES.get(league_id, 0.0)
        
        # Team Balance Estimation
        odds_diff = abs(home_odds - away_odds)
        balance_weight = 0.0
        if odds_diff < 0.5:
            balance_weight = 0.04
        elif odds_diff > 2.0:
            balance_weight = -0.05
            
        # Goal Expectancy Logic
        goal_expectancy_weight = 0.0
        if under_2_5_odds < 1.60:
            goal_expectancy_weight = 0.05
        elif under_2_5_odds > 2.10:
            goal_expectancy_weight = -0.03

        # Final AI Probability
        ai_prob = base_prob + league_weight + balance_weight + goal_expectancy_weight
        ai_prob = max(0.01, min(0.99, ai_prob))

        # 3. Value Edge
        value_edge = ai_prob - market_prob

        return {
            "home": real_names['home'],
            "away": real_names['away'],
            "league": real_names['league'],
            "draw_odds": draw_odds,
            "ai_prob": ai_prob,
            "market_prob": market_prob,
            "value_edge": value_edge,
            "fixture_id": fixture_id
        }
    except Exception as e:
        return None

async def send_telegram_message(formatted_text):
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text=formatted_text, parse_mode='HTML')

def run_system():
    odds_data, match_dictionary = get_daily_data()
    
    value_draws = []
    good_value = []
    avoid = []
    
    for match in odds_data:
        analysis = calculate_probabilities(match, match_dictionary)
        
        if analysis:
            edge = analysis['value_edge']
            # NOTE: Threshold set to 0.05 so you get more matches on your daily slip!
            if edge >= 0.05:
                value_draws.append(analysis)
            elif 0.02 <= edge < 0.05:
                good_value.append(analysis)
            else:
                avoid.append(analysis)

    # Sort by highest edge
    value_draws = sorted(value_draws, key=lambda x: x['value_edge'], reverse=True)
    good_value = sorted(good_value, key=lambda x: x['value_edge'], reverse=True)
    
    # Build Telegram Message
    msg = "🤖 <b>AI DRAW ENGINE</b>\n\n"
    
    msg += "🔥 <b>VALUE DRAWS</b>\n"
    slip_teams = []
    if not value_draws:
        msg += "No strong value draws today.\n"
    for m in value_draws[:5]:
        msg += f"{m['home']} vs {m['away']} | {m['league']}\n"
        msg += f"AI: {m['ai_prob']*100:.1f}% vs Market: {m['market_prob']*100:.1f}%\n"
        msg += f"EDGE: +{m['value_edge']:.2f} | Odds: {m['draw_odds']}\n\n"
        slip_teams.append(f"{m['home'][:3].upper()}/{m['away'][:3].upper()}")

    msg += "⚖️ <b>GOOD VALUE</b>\n"
    if not good_value:
         msg += "No good value draws today.\n"
    for m in good_value[:5]:
        msg += f"{m['home']} vs {m['away']} | {m['league']}\n"
        msg += f"EDGE: +{m['value_edge']:.2f} | Odds: {m['draw_odds']}\n\n"

    msg += "🎲 <b>AVOID (High Risk/Negative Edge)</b>\n"
    for m in avoid[:3]:
        msg += f"{m['home']} vs {m['away']} | EDGE: {m['value_edge']:.2f}\n"

    msg += "\n🧾 <b>DRW-DAILY SLIP:</b>\n"
    msg += " | ".join(slip_teams) if slip_teams else "NO SLIP TODAY"

    # Execute async send
    asyncio.run(send_telegram_message(msg))

if __name__ == "__main__":
    run_system()
