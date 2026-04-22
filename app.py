from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import requests
from nhlpy import NHLClient
from datetime import datetime
import threading

app = Flask(__name__)
CORS(app)
client = NHLClient()

# Cache for the League Ratio to prevent redundant API calls
_ratio_cache = {}
_ratio_lock = threading.Lock()

NHL_TEAMS = [
    {"abbr": "ANA", "name": "Anaheim Ducks"}, {"abbr": "BOS", "name": "Boston Bruins"},
    {"abbr": "BUF", "name": "Buffalo Sabres"}, {"abbr": "CGY", "name": "Calgary Flames"},
    {"abbr": "CAR", "name": "Carolina Hurricanes"}, {"abbr": "CHI", "name": "Chicago Blackhawks"},
    {"abbr": "COL", "name": "Colorado Avalanche"}, {"abbr": "CBJ", "name": "Columbus Blue Jackets"},
    {"abbr": "DAL", "name": "Dallas Stars"}, {"abbr": "DET", "name": "Detroit Red Wings"},
    {"abbr": "EDM", "name": "Edmonton Oilers"}, {"abbr": "FLA", "name": "Florida Panthers"},
    {"abbr": "LAK", "name": "Los Angeles Kings"}, {"abbr": "MIN", "name": "Minnesota Wild"},
    {"abbr": "MTL", "name": "Montreal Canadiens"}, {"abbr": "NSH", "name": "Nashville Predators"},
    {"abbr": "NJD", "name": "New Jersey Devils"}, {"abbr": "NYI", "name": "New York Islanders"},
    {"abbr": "NYR", "name": "New York Rangers"}, {"abbr": "OTT", "name": "Ottawa Senators"},
    {"abbr": "PHI", "name": "Philadelphia Flyers"}, {"abbr": "PIT", "name": "Pittsburgh Penguins"},
    {"abbr": "SEA", "name": "Seattle Kraken"}, {"abbr": "SJS", "name": "San Jose Sharks"},
    {"abbr": "STL", "name": "St. Louis Blues"}, {"abbr": "TBL", "name": "Tampa Bay Lightning"},
    {"abbr": "TOR", "name": "Toronto Maple Leafs"}, {"abbr": "UTA", "name": "Utah Hockey Club"},
    {"abbr": "VAN", "name": "Vancouver Canucks"}, {"abbr": "VGK", "name": "Vegas Golden Knights"},
    {"abbr": "WSH", "name": "Washington Capitals"}, {"abbr": "WPG", "name": "Winnipeg Jets"},
]

def get_season_string(offset=0):
    # calculates the NHL season string (e.g., '20252026') based on current date
    now = datetime.now()
    year = now.year - offset
    if now.month >= 9:
        return f"{year}{year + 1}"
    return f"{year - 1}{year}"

def get_true_league_ratio(season_str):
    # calculates standings to skater points ratio
    try:
        # get standings
        year_end = season_str[4:]
        date_str = f"{year_end}-04-10" # approx end of regular season
        standings_url = f"https://api-web.nhle.com/v1/standings/{date_str}"
        standings_data = requests.get(standings_url, timeout=10).json()
        total_s_points = sum(team['points'] for team in standings_data['standings'])

        # # get total player points (CRITICAL: Added limit=-1)
        stats_url = f"https://api.nhle.com/stats/rest/en/skater/summary?cayenneExp=seasonId={season_str}%20and%20gameTypeId=2&limit=-1"
        stats_data = requests.get(stats_url, timeout=10).json()
        total_p_points = sum(player['points'] for player in stats_data['data'])

        if total_p_points > 0:
            ratio = total_s_points / total_p_points
            # SANITY CHECK: if ratio is > 0.5, something is wrong with the data fetch
            return ratio if ratio < 0.5 else 0.135
    except Exception as e:
        print(f"Ratio error: {e}")
        return 0.135

def get_calibrated_ratio():
    # final ratio = avg of last 2 seasons (to reduce variance)
    with _ratio_lock: # stops all requests from trying to calibrate at the same time (which can cause API rate limits and redundant calls)
        if 'ratio' in _ratio_cache:
            return _ratio_cache['ratio']
        ratios = []
        for i in range(1, 3):
            season = get_season_string(offset=i)
            r = get_true_league_ratio(season)
            ratios.append(r)
        avg = sum(ratios) / len(ratios) if ratios else 0.135
        _ratio_cache['ratio'] = avg
        return avg

def get_player_data(p_id):
    # fetch PPG for any player ID
    url = f"https://api-web.nhle.com/v1/player/{p_id}/landing"
    resp = requests.get(url, timeout=10)
    if resp.status_code == 200:
        d = resp.json()
        s = d.get('featuredStats', {}).get('regularSeason', {}).get('subSeason', {})
        pts, gms = s.get('points', 0), s.get('gamesPlayed', 0)
        
        # sample size filter to prevent 1-game wonders from skewing the model
        ppg = pts / gms if gms > 0 else 0
        if gms < 10:
            ppg *= 0.5
            
        return {
            'id': p_id,
            'name': f"{d['firstName']['default']} {d['lastName']['default']}",
            'ppg': round(ppg, 3),
            'points': pts,
            'goals': s.get('goals', 0),
            'assists': s.get('assists', 0),
            'gamesPlayed': gms,
            'position': d.get('position', 'F'),
            'headshot': d.get('headshot', ''),
            'teamLogo': d.get('teamLogo', ''),
        }
    return None

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/api/teams')
def teams():
    return jsonify(NHL_TEAMS)

@app.route('/api/roster/<team_abbr>')
def roster(team_abbr):
    # Only the top 12 Forwards and 6 Defensemen are counted toward the projection.
    try:
        current_season = get_season_string()
        roster_data = client.teams.team_roster(team_abbr=team_abbr.upper(), season=current_season)
        full_roster_ids = [p['id'] for p in (roster_data.get('forwards', []) + roster_data.get('defensemen', []))]

        all_players = []
        for p_id in full_roster_ids:
            data = get_player_data(p_id)
            if data:
                all_players.append(data)
                
        forwards = [p for p in all_players if p['position'] in ['L', 'R', 'C']]
        defense = [p for p in all_players if p['position'] == 'D']

        # sort by offensive production
        forwards.sort(key=lambda x: x['ppg'], reverse=True)
        defense.sort(key=lambda x: x['ppg'], reverse=True)

        # take top 12 F and top 6 D
        active_forwards = forwards[:12]
        active_defense = defense[:6]
        active_lineup = active_forwards + active_defense

        league_ratio = get_calibrated_ratio()
        team_ppg = sum(p['ppg'] for p in active_lineup)
        
        # Projected Points = (Team PPG * 82 Games) * League Ratio
        projected_pts = round((team_ppg * 82) * league_ratio, 1)

        return jsonify({
            'players': all_players, 
            'activeLineupIds': [p['id'] for p in active_lineup],
            'teamPPG': round(team_ppg, 3),
            'projectedPoints': projected_pts,
            'leagueRatio': round(league_ratio, 4),
            'season': current_season,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/search-players')
def search_players():
    query = request.args.get('q', '').strip()
    if len(query) < 2: return jsonify([])
    try:
        url = f"https://search.d3.nhle.com/api/v1/search/player?culture=en-us&limit=10&q={query}&active=true"
        resp = requests.get(url, timeout=8)
        results = resp.json()
        return jsonify([{
            'id': p.get('playerId'),
            'name': p.get('name', ''),
            'teamAbbrev': p.get('teamAbbrev', ''),
            'position': p.get('positionCode', ''),
            'headshot': p.get('headshot', ''),
        } for p in results])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/simulate-trade', methods=['POST'])
def simulate_trade():
    body = request.json
    team_ppg = body.get('teamPPG', 0)
    projected_pts = body.get('projectedPoints', 0)
    out_ppg = body.get('outPPG', 0)
    in_id = body.get('inPlayerId')
    league_ratio = body.get('leagueRatio', 0.135)

    in_player = get_player_data(int(in_id))
    if not in_player:
        return jsonify({'error': 'Could not fetch incoming player data'}), 400

    new_ppg = team_ppg - out_ppg + in_player['ppg']
    new_projection = round((new_ppg * 82) * league_ratio, 1)
    diff = round(new_projection - projected_pts, 1)

    return jsonify({
        'newPPG': round(new_ppg, 3),
        'newProjection': new_projection,
        'diff': diff,
        'inPlayer': in_player,
        'improvement': diff > 0,
    })

if __name__ == '__main__':
    app.run(debug=True, port=5050)
    
# future to-do: Ensure trade impact is calculated by re-sorting the FULL roster and re-slicing the top 18 (12F/6D) 
# to account for marginal gains and ignore non-active (bench) player swaps.