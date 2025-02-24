"""
Team mapping utilities for sports betting APIs
"""

# NBA team mapping for Unabated API
NBA_TEAM_IDS = {
    63: "ATL",  # Atlanta Hawks
    64: "BOS",  # Boston Celtics
    65: "BKN",  # Brooklyn Nets
    66: "CHA",  # Charlotte Hornets
    67: "CHI",  # Chicago Bulls
    68: "CLE",  # Cleveland Cavaliers
    69: "DAL",  # Dallas Mavericks
    70: "DEN",  # Denver Nuggets
    71: "DET",  # Detroit Pistons
    72: "GSW",  # Golden State Warriors
    73: "HOU",  # Houston Rockets
    74: "IND",  # Indiana Pacers
    75: "LAC",  # Los Angeles Clippers
    76: "LAL",  # Los Angeles Lakers
    77: "MEM",  # Memphis Grizzlies
    78: "MIA",  # Miami Heat
    79: "MIL",  # Milwaukee Bucks
    80: "MIN",  # Minnesota Timberwolves
    81: "NOP",  # New Orleans Pelicans
    82: "NYK",  # New York Knicks
    83: "OKC",  # Oklahoma City Thunder
    84: "ORL",  # Orlando Magic
    85: "PHI",  # Philadelphia 76ers
    86: "PHX",  # Phoenix Suns
    87: "POR",  # Portland Trail Blazers
    88: "SAC",  # Sacramento Kings
    89: "SAS",  # San Antonio Spurs
    90: "TOR",  # Toronto Raptors
    91: "UTA",  # Utah Jazz
    92: "WAS",  # Washington Wizards
}

# Full team names to abbreviations
NBA_TEAM_ABBR = {
    "Atlanta Hawks": "ATL",
    "Boston Celtics": "BOS",
    "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI",
    "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL",
    "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW",
    "Houston Rockets": "HOU",
    "Indiana Pacers": "IND",
    "Los Angeles Clippers": "LAC",
    "LA Clippers": "LAC",
    "Los Angeles Lakers": "LAL",
    "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA",
    "Milwaukee Bucks": "MIL",
    "Minnesota Timberwolves": "MIN",
    "New Orleans Pelicans": "NOP",
    "New York Knicks": "NYK",
    "Oklahoma City Thunder": "OKC",
    "Orlando Magic": "ORL",
    "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX",
    "Portland Trail Blazers": "POR",
    "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SAS",
    "Toronto Raptors": "TOR",
    "Utah Jazz": "UTA",
    "Washington Wizards": "WAS"
}

# Abbreviations to full team names
NBA_ABBR_TO_TEAM = {v: k for k, v in NBA_TEAM_ABBR.items()}

# Polymarket slug format: nba-{away_abbr}-{home_abbr}-YYYY-MM-DD
def generate_polymarket_slug(away_team, home_team, game_date):
    """
    Generate a Polymarket slug from team names and date
    
    Args:
        away_team: Away team name
        home_team: Home team name
        game_date: Game date in YYYY-MM-DD format
    
    Returns:
        Polymarket slug string
    """
    away_abbr = get_team_abbr(away_team)
    home_abbr = get_team_abbr(home_team)
    
    if not away_abbr or not home_abbr:
        return None
        
    return f"nba-{away_abbr.lower()}-{home_abbr.lower()}-{game_date}"

def get_team_abbr(team_name):
    """
    Get team abbreviation from team name
    
    Args:
        team_name: Full or partial team name
    
    Returns:
        Team abbreviation or None if not found
    """
    # Direct match
    if team_name in NBA_TEAM_ABBR:
        return NBA_TEAM_ABBR[team_name]
    
    # Check if abbreviation already
    if team_name in NBA_ABBR_TO_TEAM:
        return team_name
        
    # Partial match
    for full_name, abbr in NBA_TEAM_ABBR.items():
        if team_name.lower() in full_name.lower():
            return abbr
            
    return None

def get_team_name(team_abbr):
    """
    Get full team name from abbreviation
    
    Args:
        team_abbr: Team abbreviation
    
    Returns:
        Full team name or None if not found
    """
    if team_abbr in NBA_ABBR_TO_TEAM:
        return NBA_ABBR_TO_TEAM[team_abbr]
    
    # Try case-insensitive match
    for abbr, name in NBA_ABBR_TO_TEAM.items():
        if team_abbr.upper() == abbr.upper():
            return name
            
    return None

def find_team_by_partial_name(partial_name):
    """
    Find team by partial name match
    
    Args:
        partial_name: Partial team name
    
    Returns:
        Full team name or None if not found
    """
    partial_name = partial_name.lower()
    
    for full_name in NBA_TEAM_ABBR.keys():
        if partial_name in full_name.lower():
            return full_name
            
    return None 