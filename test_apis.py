import requests
import os
from dotenv import load_dotenv

load_dotenv()
key = os.getenv("FOOTBALL_API_KEY")

def test_odds_api():
    print("Testing The Odds API...")
    url = f"https://api.the-odds-api.com/v4/sports/soccer/odds/?apiKey={key}&regions=eu&markets=h2h"
    try:
        response = requests.get(url)
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Success! Found {len(data)} matches.")
            # print(data[0] if data else "No data")
        else:
            print(response.text)
    except Exception as e:
        print(f"Error: {e}")

def test_football_data():
    print("\nTesting Football-Data.org...")
    url = "https://api.football-data.org/v4/matches"
    headers = {"X-Auth-Token": key}
    try:
        response = requests.get(url, headers=headers)
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Success! Found {len(data.get('matches', []))} matches.")
        else:
            print(response.text)
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_odds_api()
    test_football_data()
