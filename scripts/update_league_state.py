import json
import os
import urllib.request
from datetime import datetime, timezone

LEAGUE_ID = "1332851598593908736"
USER_ID = "731759054447321088"

BASE_URL = "https://api.sleeper.app/v1"
OUTPUT_DIR = "docs"
STATE_FILE = os.path.join(OUTPUT_DIR, "league-state.json")
PLAYER_CACHE_FILE = os.path.join(OUTPUT_DIR, "player-map.json")


def fetch_json(path):
    url = f"{BASE_URL}{path}"
    print(f"Fetching {url}")
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def player_cache_is_fresh():
    if not os.path.exists(PLAYER_CACHE_FILE):
        return False

    modified = datetime.fromtimestamp(
        os.path.getmtime(PLAYER_CACHE_FILE),
        tz=timezone.utc
    )

    age_hours = (
        datetime.now(timezone.utc) - modified
    ).total_seconds() / 3600

    return age_hours < 24


def load_players():
    if player_cache_is_fresh():
        print("Using existing player cache.")
        with open(PLAYER_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    print("Refreshing Sleeper player database.")
    players = fetch_json("/players/nfl")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    with open(PLAYER_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(players, f)

    return players


def player_info(player_id, players):
    data = players.get(str(player_id), {})

    full_name = data.get("full_name")
    if not full_name:
        first = data.get("first_name") or ""
        last = data.get("last_name") or ""
        full_name = f"{first} {last}".strip()

    return {
        "player_id": str(player_id),
        "name": full_name or str(player_id),
        "position": data.get("position"),
        "team": data.get("team"),
        "status": data.get("status"),
    }


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    league = fetch_json(f"/league/{LEAGUE_ID}")
    users = fetch_json(f"/league/{LEAGUE_ID}/users")
    rosters = fetch_json(f"/league/{LEAGUE_ID}/rosters")
    traded_picks = fetch_json(f"/league/{LEAGUE_ID}/traded_picks")
    nfl_state = fetch_json("/state/nfl")
    players = load_players()

    users_by_id = {
        str(user["user_id"]): user
        for user in users
    }

    normalized_rosters = []

    for roster in rosters:
        owner_id = str(roster.get("owner_id"))

        roster_players = [
            player_info(pid, players)
            for pid in roster.get("players", [])
        ]

        starters = [
            player_info(pid, players)
            for pid in roster.get("starters", [])
            if str(pid) != "0"
        ]

        starter_ids = {
            str(pid)
            for pid in roster.get("starters", [])
        }

        bench = [
            p for p in roster_players
            if p["player_id"] not in starter_ids
        ]

        owner = users_by_id.get(owner_id, {})

        normalized_rosters.append({
            "roster_id": roster.get("roster_id"),
            "owner_id": owner_id,
            "display_name": owner.get("display_name"),
            "team_name": owner.get("metadata", {}).get("team_name"),
            "players": roster_players,
            "starters": starters,
            "bench": bench,
            "taxi": [
    player_info(pid, players)
    for pid in (roster.get("taxi") or [])
],
"reserve": [
    player_info(pid, players)
    for pid in (roster.get("reserve") or [])
],
            "reserve": [
                player_info(pid, players)
                for pid in roster.get("reserve", [])
            ],
            "settings": roster.get("settings", {}),
        })

    my_roster = next(
        (
            roster
            for roster in normalized_rosters
            if roster["owner_id"] == USER_ID
        ),
        None
    )

    state = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "Sleeper API",
        "league_id": LEAGUE_ID,
        "user_id": USER_ID,
        "nfl_state": nfl_state,
        "league": league,
        "my_roster": my_roster,
        "all_rosters": normalized_rosters,
        "traded_picks": traded_picks,
    }

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

    if my_roster:
        print(
            f"Success: found roster {my_roster['roster_id']} "
            f"for {my_roster['display_name']}"
        )
    else:
        raise RuntimeError(
            f"Could not find roster owned by Sleeper user {USER_ID}"
        )

    print(f"Wrote {STATE_FILE}")


if __name__ == "__main__":
    main()
