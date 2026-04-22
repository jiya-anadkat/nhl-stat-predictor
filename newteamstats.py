import pandas as pd
import requests
from nhlpy import NHLClient
from datetime import datetime

client = NHLClient()
team_abbr = "SJS" 

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
        standings_data = requests.get(standings_url).json() # change to dict for easier access
        total_s_points = sum(team['points'] for team in standings_data['standings'])
        
        # get total player points (CRITICAL: Added limit=-1)
        stats_url = f"https://api.nhle.com/stats/rest/en/skater/summary?cayenneExp=seasonId={season_str}%20and%20gameTypeId=2&limit=-1"
        stats_data = requests.get(stats_url).json()
        total_p_points = sum(player['points'] for player in stats_data['data'])
        
        if total_p_points > 0:
            ratio = total_s_points / total_p_points
            
            # SANITY CHECK: if ratio is > 0.5, something is wrong with the data fetch
            if ratio > 0.5:
                print(f"Warning: Ratio for {season_str} looks high ({round(ratio, 3)}). Defaulting.")
                return 0.135 # fallback based on avg from 20242025
            return ratio
            
    except Exception as e: # print error and return fallback
        print(f"⚠️ CALIBRATION FAILED: {e}")
        print("Using fallback ratio: 0.135") 
        return 0.135

def calibrate_league_ratio(years_back=2):
    # normalize the ratio over multiple seasons to reduce variance
    ratios = []
    for i in range(1, years_back + 1):
        season = get_season_string(offset=i)
        print(f"Calibrating using {season} data...")
        ratio = get_true_league_ratio(season)
        ratios.append(ratio)
    
    avg_ratio = sum(ratios) / len(ratios) if ratios else 0.135
    print(f"Final Calibrated Ratio: {round(avg_ratio, 4)}\n")
    return avg_ratio

def get_player_data(p_id):
    # fetch PPG for any player ID
    url = f"https://api-web.nhle.com/v1/player/{p_id}/landing"
    resp = requests.get(url)
    if resp.status_code == 200:
        d = resp.json()
        s = d.get('featuredStats', {}).get('regularSeason', {}).get('subSeason', {})
        pts = s.get('points', 0)
        gms = s.get('gamesPlayed', 0)
        
        ppg = pts / gms if gms > 0 else 0
        if gms < 10: ppg *= 0.5
            
        return {'id': p_id, 'name': f"{d['firstName']['default']} {d['lastName']['default']}", 'ppg': ppg}
    return None

league_ratio = calibrate_league_ratio(years_back=2)
current_season = get_season_string()

print(f"--- Analyzing {team_abbr} Roster for {current_season} ---")
roster_data = client.teams.team_roster(team_abbr=team_abbr, season=current_season)
full_roster = roster_data.get('forwards', []) + roster_data.get('defensemen', [])

player_stats = []
for row in full_roster:
    data = get_player_data(row['id'])
    if data: player_stats.append(data)

df_final = pd.DataFrame(player_stats)
team_total_ppg = df_final['ppg'].sum()

projected_points = round((team_total_ppg * 82) * league_ratio, 1)

print("\n--- Team Strength Analysis ---")
print(f"Total Team PPG: {round(team_total_ppg, 3)}")
print(f"Projected Standings Points: {projected_points}")

def simulate_trade(player_out_name, player_in_id):
    # Swaps a current roster player for a new player ID and shows the impact.
    # fetch new player data
    new_player = get_player_data(player_in_id)
    if not new_player:
        print("Could not find data for the new player.")
        return

    # find the outgoing player in your existing df
    out_mask = df_final['name'].str.contains(player_out_name, case=False)
    if not out_mask.any():
        print(f"Could not find '{player_out_name}' on the current roster.")
        return
    outgoing_player = df_final[out_mask].iloc[0]
    
    # calculate the PPG Delta
    # new total = (old total - leaving PPG + incoming PPG)
    new_team_ppg = team_total_ppg - outgoing_player['ppg'] + new_player['ppg']
    new_projection = round((new_team_ppg * 82) * league_ratio, 1)
    
    # calculate the difference and impact
    diff = round(new_projection - projected_points, 1)
    impact_msg = "🔥 IMPROVEMENT" if diff > 0 else "❄️ DECLINE"
    
    print(f"\n--- TRADE SIMULATION: {impact_msg} ---")
    print(f"REMOVING: {outgoing_player['name']} ({round(outgoing_player['ppg'], 2)} PPG)")
    print(f"ADDING:   {new_player['name']} ({round(new_player['ppg'], 2)} PPG)")
    print(f"-----------------------------------------")
    print(f"OLD Projection: {projected_points}")
    print(f"NEW Projection: {new_projection}")
    print(f"NET IMPACT:     {diff} Standings Points")
    
print("\n" + "="*30)
print("NHL TRADE SIMULATOR")
print("="*30)

user_choice = input("Would you like to simulate a trade? (yes/no): ").lower()

if user_choice == "yes":
    out_name = input("Enter the last name of the player to TRADE AWAY: ")
    in_id = input("Enter the NHL ID of the player to ACQUIRE: ")
    simulate_trade(out_name, int(in_id))
else:
    print("Exiting simulator. Good luck with the season!")

# simulate_trade("Celebrini", 8478402)