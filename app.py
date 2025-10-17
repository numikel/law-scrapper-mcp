from fastmcp import FastMCP
import requests
from datetime import datetime
from dateutil.relativedelta import relativedelta

app = FastMCP("law-scrapper-mcp")

@app.tool()
def get_actual_date() -> str:
    """
    Returns the actual date.
    """
    try:
        return datetime.now().strftime("%Y-%m-%d")
    except Exception as e:
        print(f"Error: {e}")
        return []

@app.tool()
def calculate_previous_date(actual_date: str, days: int = 0, months: int = 0, years: int = 0) -> str:
    """
    Calculates the previous date based on the actual date and the number of days, months, and years.
    """
    try:
        return (datetime.strptime(actual_date, "%Y-%m-%d") - relativedelta(days=days, months=months, years=years)).strftime("%Y-%m-%d")
    except Exception as e:
        print(f"Error: {e}")
        return actual_date

@app.tool()
def get_keywords_list() -> list[str]:
    """
    Retrieves a list of available keywords for law acts from the Sejm API.
    Returns:
        list: List of keywords.
    """
    try:
        url = "https://api.sejm.gov.pl/eli/keywords"
        response = requests.get(url)
        data = response.json()
        return data
    except Exception as e:
        print(f"Error: {e}")
        return []

def get_acts_list(self, year: int = None, keywords: list = None, date_from: str = None, date_to: str = None) -> list:
    """
    Fetches a list of legal acts from the Sejm API based on specified filters.

    Parameters:
        year (int, optional): Year of publication.
        keywords (list, optional): List of keywords to filter the acts.
        date_from (str, optional): Starting date of effectiveness (YYYY-MM-DD).
        date_to (str, optional): Ending date of effectiveness (YYYY-MM-DD).

    Returns:
        list: A list of legal acts matching the criteria.
    """
    try:
        params = {
            "publisher": "DU",
        }
        if (year):
            params["year"] = year
        if (keywords):
            params["keyword"] = ",".join(keywords)
        if (date_from):
            params["dateEffectFrom"] = date_from.strftime("%Y-%m-%d")
        if (date_to):
            params["dateEffectTo"] = date_to.strftime("%Y-%m-%d")

        url = "https://api.sejm.gov.pl/eli/acts/search"

        # Log the full URL with parameters for debugging purposes, but use the original requests.get with params
        full_url = requests.Request('GET', url, params=params).prepare().url
        response = requests.get(url, params=params, headers={"Accept": "application/json"})

        if response.status_code == 200:
            data = response.json().get("items", [])
        else:
            data = []

        if not data:
            return []

        return data
    except Exception as e:
        print(f"Error: {e}")
        return []

def main():
    app.run()

if __name__ == "__main__":
    main()