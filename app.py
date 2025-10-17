from fastmcp import FastMCP
import requests

app = FastMCP("law-scrapper-mcp")

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

def main():
    app.run()

if __name__ == "__main__":
    main()