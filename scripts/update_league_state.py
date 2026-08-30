import json
import os
import urllib.request
from datetime import datetime, timezone

# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------

LEAGUE_ID = "1332851598593908736"

# Stable Sleeper user ID for Doctorie.
# We use owner_id rather than assuming roster_id stays constant.
USER_ID = "731759054447321088"

BASE_URL = "https://api.sleeper.app/v1"

OUTPUT_DIR = "docs"
STATE_FILE = os.path.join(OUTPUT_DIR, "league-state.json")
PLAYER_CACHE_FILE = os.path.join(OUTPUT_DIR, "player-map.json")


# ---------------------------------------------------------
# SLEEPER API
# ---------------------------------------------------------

def fetch_json(path):
    url = f"{BASE_URL}{path}"
    print(f"Fetching {url}")

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "fantasy-football-league-state/1.0"
        },
    )

    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


# ---------------------------------------------------------
# PLAYER DATABASE
# ---------------------------------------------------------

def player_cache_is_fresh():
    if not os.path.exists(PLAYER_CACHE_FILE):
        return False

    modified = datetime.fromtimestamp(
        os.path.getmtime(PLAYER_CACHE_FILE),
        tz=timezone.utc,
    )

    age_hours = (
        datetime.now(timezone.utc) - modified
    ).total_seconds() / 3600

    # Sleeper recommends not repeatedly downloading the
    # complete player database.
    return age_hours < 24


def load_players():
    if player_cache_is_fresh():
        print("Using existing player cache.")

        with open(
            PLAYER_CACHE_FILE,
            "r",
            encoding="utf-8",
        ) as f:
            return json.load(f)

    print("Refreshing Sleeper player database.")

    players = fetch_json("/players/nfl")

    if not isinstance(players, dict):
        raise RuntimeError(
            "Sleeper player database did not return a dictionary."
        )

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    with open(
        PLAYER_CACHE_FILE,
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(players, f)

    return players


def player_info(player_id, players):
    player_id = str(player_id)

    data = players.get(player_id, {}) or {}

    full_name = data.get("full_name")

    if not full_name:
        first = data.get("first_name") or ""
        last = data.get("last_name") or ""
        full_name = f"{first} {last}".strip()

    return {
        "player_id": player_id,
        "name": full_name or player_id,
        "position": data.get("position"),
        "team": data.get("team"),
        "status": data.get("status"),
    }


# ---------------------------------------------------------
# NORMALIZATION HELPERS
# ---------------------------------------------------------

def safe_list(value):
    """
    Sleeper sometimes returns null instead of [].
    Convert None or other false values to an empty list.
    """
    return value or []


def normalize_player_list(player_ids, players):
    return [
        player_info(pid, players)
        for pid in safe_list(player_ids)
        if pid is not None and str(pid) != "0"
    ]


# ---------------------------------------------------------
# MAIN UPDATE
# ---------------------------------------------------------

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # -----------------------------------------------------
    # Fetch current league information
    # -----------------------------------------------------

    league = fetch_json(
        f"/league/{LEAGUE_ID}"
    )

    users = fetch_json(
        f"/league/{LEAGUE_ID}/users"
    )

    rosters = fetch_json(
        f"/league/{LEAGUE_ID}/rosters"
    )

    traded_picks = fetch_json(
        f"/league/{LEAGUE_ID}/traded_picks"
    )

    nfl_state = fetch_json(
        "/state/nfl"
    )

    players = load_players()

    if not isinstance(users, list):
        raise RuntimeError(
            "Sleeper users endpoint did not return a list."
        )

    if not isinstance(rosters, list):
        raise RuntimeError(
            "Sleeper rosters endpoint did not return a list."
        )

    # -----------------------------------------------------
    # Map Sleeper users
    # -----------------------------------------------------

    users_by_id = {
        str(user.get("user_id")): user
        for user in users
        if user.get("user_id") is not None
    }

    # -----------------------------------------------------
    # Normalize every league roster
    # -----------------------------------------------------

    normalized_rosters = []

    for roster in rosters:

        owner_id = roster.get("owner_id")

        if owner_id is not None:
            owner_id = str(owner_id)

        owner = users_by_id.get(
            owner_id,
            {},
        ) or {}

        roster_player_ids = safe_list(
            roster.get("players")
        )

        starter_player_ids = safe_list(
            roster.get("starters")
        )

        taxi_player_ids = safe_list(
            roster.get("taxi")
        )

        reserve_player_ids = safe_list(
            roster.get("reserve")
        )

        roster_players = normalize_player_list(
            roster_player_ids,
            players,
        )

        starters = normalize_player_list(
            starter_player_ids,
            players,
        )

        taxi = normalize_player_list(
            taxi_player_ids,
            players,
        )

        reserve = normalize_player_list(
            reserve_player_ids,
            players,
        )

        starter_ids = {
            str(pid)
            for pid in starter_player_ids
            if pid is not None and str(pid) != "0"
        }

        taxi_ids = {
            str(pid)
            for pid in taxi_player_ids
            if pid is not None and str(pid) != "0"
        }

        reserve_ids = {
            str(pid)
            for pid in reserve_player_ids
            if pid is not None and str(pid) != "0"
        }

        # Bench means players on the roster who are not
        # currently starters, taxi, or reserve.
        bench = [
            player
            for player in roster_players
            if player["player_id"] not in starter_ids
            and player["player_id"] not in taxi_ids
            and player["player_id"] not in reserve_ids
        ]

        metadata = owner.get("metadata") or {}

        normalized_rosters.append({
            "roster_id": roster.get("roster_id"),
            "owner_id": owner_id,
            "display_name": owner.get("display_name"),
            "team_name": metadata.get("team_name"),
            "players": roster_players,
            "starters": starters,
            "bench": bench,
            "taxi": taxi,
            "reserve": reserve,
            "settings": roster.get("settings") or {},
        })

    # -----------------------------------------------------
    # Resolve Doctorie's roster using stable Sleeper ID
    # -----------------------------------------------------

    my_roster = next(
        (
            roster
            for roster in normalized_rosters
            if roster["owner_id"] == USER_ID
        ),
        None,
    )

    if my_roster is None:
        raise RuntimeError(
            "Could not find a roster owned by Sleeper "
            f"user ID {USER_ID}."
        )

    # -----------------------------------------------------
    # Build shared league-state file
    # -----------------------------------------------------

    generated_at = datetime.now(
        timezone.utc
    ).isoformat()

    state = {
        "schema_version": "1.0",
        "generated_at_utc": generated_at,
        "source": "Sleeper API",
        "league_id": LEAGUE_ID,
        "user_id": USER_ID,
        "freshness": {
            "generated_at_utc": generated_at,
            "refresh_target_minutes": 15,
        },
        "nfl_state": nfl_state,
        "league": league,
        "my_roster": my_roster,
        "all_rosters": normalized_rosters,
        "traded_picks": traded_picks or [],
    }

    # -----------------------------------------------------
    # Write output
    # -----------------------------------------------------

    with open(
        STATE_FILE,
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            state,
            f,
            indent=2,
            ensure_ascii=False,
        )

    print(
        "Success: found roster "
        f"{my_roster['roster_id']} "
        f"for {my_roster['display_name']}."
    )

    print(
        "Roster contains "
        f"{len(my_roster['players'])} active roster players, "
        f"{len(my_roster['taxi'])} taxi players, and "
        f"{len(my_roster['reserve'])} reserve players."
    )

    print(
        f"Wrote {STATE_FILE}"
    )


if __name__ == "__main__":
    main()
