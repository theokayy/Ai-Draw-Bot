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
# Serie A (135), Ligue 1 (61), Arg Primera (128), USL (253), NPFL (284)
HIGH_DRAW_LEAGUES = {135: 0.04, 61: 0.04, 128: 0.05, 253: 0.03, 284: 0.06}
# Bundesliga (78), Eredivisie (88)
LOW_DRAW_LEAGUES = {78: -0.04, 88: -0.05}

def get_todays_odds():
    """Fetch today's matches and their pre-match odds in one call to save API limits."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    endpoint = f"{BASE_URL}/odds?date={today}&bookmaker=1" # Bookmaker 1 is typically 10bet/Bet365
    response = requests.get(endpoint, headers=HEADERS)
    if response.status_code != 200:
        return []
    
    data = response.json().get('response', [])
    return data

def calculate_probabilities(match_data):
    """Calculates AI Probability, Market Probability, and Value Edge."""
    try:
        fixture = match_data['fixture']
        league = match_data['league']
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

        # Extract Under 2.5 Goals Odds (Id 5) for Goal Expectancy Logic
        ou_bet = next((b for b in bets if b['id'] == 5), None)
        under_2_5_odds = 2.0 # Default baseline
        if ou_bet:
            try:
                under_2_5_odds = float(next(v['odd'] for v in ou_bet['values'] if v['value'] == 'Under 2.5'))
            except StopIteration:
                pass

        # 1. Market Probability
        market_prob = 1 / draw_odds

        # 2. AI Probability Calculation
        # Base probability derived from global average
        base_prob = 0.28 
        
        # League Tendency
        league_id = league['id']
        league_weight = HIGH_DRAW_LEAGUES.get(league_id, 0.0) + LOW_DRAW_LEAGUES.get(league_id, 0.0)
        
        # Team Balance Estimation (Elo-style heuristic)
        # Closer odds between Home and Away = tighter match = higher draw probability
        odds_diff = abs(home_odds - away_odds)
        balance_weight = 0.0
        if odds_diff < 0.5:
            balance_weight = 0.04
        elif odds_diff > 2.0:
            balance_weight = -0.05
            
        # Goal Expectancy Logic (Low scoring games draw more often)
        # If Under 2.5 odds are low (e.g., < 1.70), market expects few goals
        goal_expectancy_weight = 0.0
        if under_2_5_odds < 1.60:
            goal_expectancy_weight = 0.05
        elif under_2_5_odds > 2.10:
            goal_expectancy_weight = -0.03

        # Final AI Probability
        ai_prob = base_prob + league_weight + balance_weight + goal_expectancy_weight
        
        # Cap probabilities between 0 and 1
        ai_prob = max(0.01, min(0.99, ai_prob))

        # 3. Value Edge
        value_edge = ai_prob - market_prob

        return {
            "home": match_data['fixture']['teams']['home']['name'] if 'teams' in match_data['fixture'] else "Home", # API odds endpoint structure adjustment
            "away": match_data['fixture']['teams']['away']['name'] if 'teams' in match_data['fixture'] else "Away",
            "league": league['name'],
            "draw_odds": draw_odds,
            "ai_prob": ai_prob,
            "market_prob": market_prob,
            "value_edge": value_edge,
            "fixture_id": fixture['id']
        }
    except Exception as e:
        return None

async def send_telegram_message(formatted_text):
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text=formatted_text, parse_mode='HTML')

def run_system():
    odds_data = get_todays_odds()
    
    value_draws = []
    good_value = []
    avoid = []
    
    # We will fetch team names by matching fixture IDs since the /odds endpoint 
    # sometimes omits team names depending on the API subscription tier.
    # For a production system with API-Sports, we fetch /fixtures first, but 
    # to maintain high efficiency, we extract names assuming standard payload.
    
    for match in odds_data:
        # Hack to get names from odds endpoint if teams object is missing
        try:
             home_team = match['fixture']['match'].split(' - ')[0] if 'match' in match['fixture'] else f"Team {match['fixture']['id']}"
             away_team = match['fixture']['match'].split(' - ')[1] if 'match' in match['fixture'] else ""
        except:
             home_team, away_team = "Home", "Away"

        analysis = calculate_probabilities(match)
        
        if analysis:
            # Overwrite with better names if available
            if 'home' in analysis and analysis['home'] != "Home":
                pass # Used the one from calculate_probabilities
            else:
                analysis['home'] = home_team
                analysis['away'] = away_team

            edge = analysis['value_edge']
            if edge >= 0.10:
                value_draws.append(analysis)
            elif 0.05 <= edge < 0.10:
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
    for m in value_draws[:5]: # Limit to top 5 to avoid message length limits
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
