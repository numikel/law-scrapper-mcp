"""
Law Scrapper MCP Server

A comprehensive Model Context Protocol server for accessing Polish legal acts (akty prawne)
from the Sejm API. Provides tools for searching, retrieving, and analyzing legal documents.

Features:
- Date and time utilities for legal document analysis
- System metadata for publishers, statuses, types, and institutions
- Legal act browsing and advanced search capabilities
- Detailed act information and content retrieval
- Act structure analysis and reference tracking

Tags:
- dates: Date and time calculation utilities
- metadata: System reference data (publishers, statuses, types, institutions)
- search: Legal act search and browsing functionality
- analysis: Detailed act content and structure analysis
"""

from fastmcp import FastMCP
import requests
from datetime import datetime
from dateutil.relativedelta import relativedelta
from typing import Annotated, Union

# Initialize FastMCP server with comprehensive description
app = FastMCP(
    name="law-scrapper-mcp",
    instructions="""
    You are a specialized assistant for Polish legal research and analysis.

    Use the available tools to:
    1. Search and browse Polish legal acts from Dziennik Ustaw and Monitor Polski
    2. Retrieve detailed information about specific legal documents
    3. Analyze legal act structures and references
    4. Access system metadata for legal document classification

    Always prefer specific searches over broad queries. When analyzing legal acts,
    consider their status, effective dates, and relationships to other documents.

    Available tool categories:
    - dates: Date calculation utilities
    - metadata: Reference data for publishers, statuses, types, institutions
    - search: Legal act discovery and filtering
    - analysis: Detailed document examination
    """
)

# ========================================
# DATES AND TIME
# ========================================

@app.tool(
    name="get_current_date",
    description="Get the current date in YYYY-MM-DD format. Useful for legal document analysis and date calculations.",
    tags={"dates", "utility"}
)
def get_actual_date() -> str:
    """
    Returns the current date in YYYY-MM-DD format for legal research and document analysis.

    Returns:
        str: Current date string in YYYY-MM-DD format

    Examples:
        User asks: "What is today's date?":
            Returns: "2025-01-17"
        User asks: "Show me the current date for legal document analysis":
            Returns: "2025-01-17"
        User asks: "Give me today's date in YYYY-MM-DD format":
            Returns: "2025-01-17"
        User asks: "What date is it right now?":
            Returns: "2025-01-17"
        User asks: "I need the current date for my legal report":
            Returns: "2025-01-17"
    """
    try:
        return datetime.now().strftime("%Y-%m-%d")
    except Exception as e:
        print(f"Error: {e}")
        return ""

@app.tool(
    name="calculate_date_offset",
    description="Calculate dates in the past or future by adding/subtracting time periods. Essential for legal document effective dates and deadlines.",
    tags={"dates", "calculation", "legal-analysis"}
)
def calculate_previous_date(
    actual_date: Annotated[str, "Starting date in YYYY-MM-DD format"],
    days: Annotated[int, "Number of days to subtract (use negative for future dates)"] = 0,
    months: Annotated[int, "Number of months to subtract (use negative for future dates)"] = 0,
    years: Annotated[int, "Number of years to subtract (use negative for future dates)"] = 0
) -> str:
    """
    Calculates a date by adding or subtracting specified time periods from a given date.
    Essential for legal analysis of effective dates, deadlines, and document validity periods.

    Parameters:
        actual_date: Starting date in YYYY-MM-DD format
        days: Number of days to add/subtract (negative = future, positive = past)
        months: Number of months to add/subtract (negative = future, positive = past)
        years: Number of years to add/subtract (negative = future, positive = past)

    Returns:
        str: Calculated date in YYYY-MM-DD format

    Examples:
        User asks: "What was the date 30 days ago from 2025-01-01?":
            Parameters: actual_date='2025-01-01', days=30
        User asks: "Calculate date 6 months before 2024-07-15":
            Parameters: actual_date='2024-07-15', months=6
        User asks: "What date will it be 2 years from today?":
            Parameters: actual_date='2025-01-17', years=-2
        User asks: "Go back 1 year and 3 months from 2023-12-31":
            Parameters: actual_date='2023-12-31', months=3, years=1
        User asks: "What was the date exactly 90 days before 2024-04-01?":
            Parameters: actual_date='2024-04-01', days=90
        User asks: "Calculate effective date - subtract 14 days from 2025-03-01":
            Parameters: actual_date='2025-03-01', days=14
        User asks: "What date was it 1 year, 2 months, and 5 days ago from 2024-12-25?":
            Parameters: actual_date='2024-12-25', days=5, months=2, years=1
    """
    try:
        return (datetime.strptime(actual_date, "%Y-%m-%d") - relativedelta(days=days, months=months, years=years)).strftime("%Y-%m-%d")
    except Exception as e:
        print(f"Error: {e}")
        return actual_date

# ========================================
# SYSTEM METADATA
# ========================================

@app.tool(
    name="get_legal_keywords",
    description="Retrieve all available keywords for categorizing Polish legal acts. Essential for understanding document topics and enabling precise searches.",
    tags={"metadata", "keywords", "reference", "search-filters"}
)
def get_keywords_list() -> list[str]:
    """
    Retrieves a list of all available keywords for law acts from the Sejm API.

    Returns:
        list[str]: List of keywords used to categorize law acts

    Examples:
        User asks: "What keywords are available for searching law acts?":
            Returns: ['sąd', 'podatek', 'prawo', ...]
        User asks: "Show me all possible keywords for legal documents":
            Returns: ['administracja', 'zdrowie', 'edukacja', ...]
        User asks: "What topics can I search for in law acts?":
            Returns: ['gospodarka', 'środowisko', 'transport', ...]
        User asks: "Give me the complete list of keywords":
            Returns: ['prawo pracy', 'podatki', 'ochrony zdrowia', ...]
        User asks: "What categories exist for Polish legal acts?":
            Returns: ['sądownictwo', 'administracja publiczna', 'prawo karne', ...]
        User asks: "I need keywords for environmental law acts":
            Returns: ['środowisko', 'ochrona przyrody', 'gospodarka odpadami', ...]
        User asks: "Show me keywords related to education":
            Returns: ['szkolnictwo', 'edukacja', 'nauka', 'uczelnie', ...]
    """
    try:
        url = "https://api.sejm.gov.pl/eli/keywords"
        response = requests.get(url)
        data = response.json()
        return data
    except Exception as e:
        print(f"Error: {e}")
        return []

# ========================================
# ACTS BROWSING AND SEARCH
# ========================================

@app.tool(
    name="search_legal_acts",
    description="Advanced search for Polish legal acts with multiple filters. Use this for finding specific documents by criteria like date, type, keywords, or title.",
    tags={"search", "acts", "filtering", "legal-research"}
)
def get_acts_list(
    year: Annotated[Union[int, str, None], "Publication year (e.g., 2020, 2023)"] = None,
    keywords: Annotated[list[str] | None, "List of keywords to search in act content"] = None,
    date_from: Annotated[str | None, "Start date for effectiveness period (YYYY-MM-DD)"] = None,
    date_to: Annotated[str | None, "End date for effectiveness period (YYYY-MM-DD)"] = None,
    title: Annotated[str | None, "Text fragment to search in act titles"] = None,
    act_type: Annotated[str | None, "Document type (e.g., 'Rozporządzenie', 'Ustawa')"] = None,
    pub_date_from: Annotated[str | None, "Start date for publication period (YYYY-MM-DD)"] = None,
    pub_date_to: Annotated[str | None, "End date for publication period (YYYY-MM-DD)"] = None,
    in_force: Annotated[Union[bool, str], "Only return currently active acts. Type 'true' for active, 'false' for inactive"] = None,
    limit: Annotated[Union[int, str, None], "Maximum number of results (default: all matching)"] = None,
    offset: Annotated[Union[int, str, None], "Skip first N results for pagination"] = None
) -> list:
    """
    Fetches a list of legal acts from the Sejm API based on specified filters.

    Parameters:
        year (int, optional): Year of publication
        keywords (list, optional): List of keywords to filter the acts
        date_from (str, optional): Starting date of effectiveness (YYYY-MM-DD)
        date_to (str, optional): Ending date of effectiveness (YYYY-MM-DD)
        title (Union[str, list[str]], optional): Fragment of title to search for
        act_type (str, optional): Type of act (e.g., 'Rozporządzenie', 'Ustawa')
        pub_date_from (str, optional): Starting publication date (YYYY-MM-DD)
        pub_date_to (str, optional): Ending publication date (YYYY-MM-DD)
        in_force (Union[bool, str], optional): Only return acts that are currently in force. Type 'true' for active, 'false' for inactive
        limit (Union[int, str], optional): Maximum number of results to return
        offset (Union[int, str], optional): Starting index for pagination

    Returns:
        list: A list of legal acts matching the criteria.

    Examples:
        User asks: "Please fetch all acts from the year 2020":
            Parameters: year = 2020
        User asks: "Please fetch all regulations from 2020":
            Parameters: year = 2020, act_type = 'Rozporządzenie'
        User asks: "Please fetch all acts with keywords 'sąd' and 'prawnik'":
            Parameters: keywords = ['sąd', 'prawnik']
        User asks: "Please fetch all currently active acts from 2020":
            Parameters: year = 2020, in_force = True
        User asks: "Please fetch acts containing 'zmieniające' in title from 2020":
            Parameters: year = 2020, title = 'zmieniające'
        User asks: "Please fetch first 10 acts from 2020":
            Parameters: year = 2020, limit = 10
    """
    try:
        params = {
            "publisher": "DU",
        }
        if (year):
            params["year"] = int(year) if isinstance(year, str) else year
        if (keywords):
            params["keyword"] = ",".join(keywords)
        if (date_from):
            params["dateEffectFrom"] = date_from
        if (date_to):
            params["dateEffectTo"] = date_to
        if (title):
            params["title"] = title
        if (act_type):
            params["type"] = act_type
        if (pub_date_from):
            params["dateFrom"] = pub_date_from
        if (pub_date_to):
            params["dateTo"] = pub_date_to
        if (in_force is not None):
            params["inForce"] = bool(in_force)
        if (limit):
            params["limit"] = int(limit) if isinstance(limit, str) else limit
        if (offset):
            params["offset"] = int(offset) if isinstance(offset, str) else offset

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

@app.tool(
    name="get_legal_publishers",
    description="Get list of all legal act publishers (Dziennik Ustaw, Monitor Polski) with their metadata and publication years.",
    tags={"metadata", "publishers", "reference", "sources"}
)
def get_publishers_list() -> list:
    """
    Fetches a list of all available legal act publishers (journals) from the Sejm API.

    Returns:
        list: A list of publisher objects containing code, name, shortName, actsCount, and years.

    Examples:
        User asks: "What publishers are available?":
            Returns: [{'code': 'DU', 'name': 'Dziennik Ustaw', 'actsCount': 96086}, {'code': 'MP', 'name': 'Monitor Polski', 'actsCount': 65485}]
        User asks: "Show me all legal act publishers":
            Returns: [{'code': 'DU', 'name': 'Dziennik Ustaw', 'actsCount': 96086}, ...]
        User asks: "What journals publish Polish law acts?":
            Returns: [{'code': 'DU', 'name': 'Dziennik Ustaw', 'actsCount': 96086}, {'code': 'MP', 'name': 'Monitor Polski', 'actsCount': 65485}]
        User asks: "List all available publishers":
            Returns: [{'code': 'DU', 'name': 'Dziennik Ustaw', 'actsCount': 96086}, {'code': 'MP', 'name': 'Monitor Polski', 'actsCount': 65485}]
        User asks: "What sources contain Polish legal acts?":
            Returns: [{'code': 'DU', 'name': 'Dziennik Ustaw', 'actsCount': 96086}, {'code': 'MP', 'name': 'Monitor Polski', 'actsCount': 65485}]
    """
    try:
        url = "https://api.sejm.gov.pl/eli/acts"
        response = requests.get(url, headers={"Accept": "application/json"})

        if response.status_code == 200:
            return response.json()
        else:
            return []
    except Exception as e:
        print(f"Error: {e}")
        return []

@app.tool(
    name="get_publisher_details",
    description="Get detailed information about a specific legal publisher including act counts and publication timeline.",
    tags={"metadata", "publishers", "reference", "sources"}
)
def get_publisher_info(
    publisher: Annotated[str, "Publisher code (DU for Dziennik Ustaw, MP for Monitor Polski)"]
) -> dict:
    """
    Fetches detailed information about a specific legal act publisher.

    Parameters:
        publisher (str): Publisher code (e.g., 'DU' for Dziennik Ustaw, 'MP' for Monitor Polski)

    Returns:
        dict: Detailed information about the publisher, or empty dict if not found.

    Examples:
        User asks: "Tell me about DU publisher":
            Parameters: publisher = 'DU'
            Returns: {'code': 'DU', 'name': 'Dziennik Ustaw', 'shortName': 'Dz.U.', 'actsCount': 96086, 'years': [1918, 1919, ...]}
        User asks: "What is the Dziennik Ustaw?":
            Parameters: publisher = 'DU'
        User asks: "Give me details about Monitor Polski":
            Parameters: publisher = 'MP'
        User asks: "How many acts are in DU?":
            Parameters: publisher = 'DU'
        User asks: "What years are covered by MP?":
            Parameters: publisher = 'MP'
    """
    try:
        url = f"https://api.sejm.gov.pl/eli/acts/{publisher}"
        response = requests.get(url, headers={"Accept": "application/json"})

        if response.status_code == 200:
            return response.json()
        else:
            return {}
    except Exception as e:
        print(f"Error: {e}")
        return {}

@app.tool(
    name="get_publisher_year_acts",
    description="Get all legal acts published by a specific publisher in a given year. Useful for browsing complete annual collections.",
    tags={"search", "acts", "yearly", "browsing"}
)
def get_year_acts(
    publisher: Annotated[str, "Publisher code (DU for Dziennik Ustaw, MP for Monitor Polski)"],
    year: Annotated[Union[int, str], "Publication year (e.g., 2020, 2023)"]
) -> dict:
    """
    Fetches a list of all legal acts for a specific publisher and year.

    Parameters:
        publisher (str): Publisher code (e.g., 'DU' for Dziennik Ustaw)
        year (int): Year of publication

    Returns:
        dict: Object containing totalCount, items array, and searchQuery info.

    Examples:
        User asks: "Show me all acts from DU in 2020":
            Parameters: publisher = 'DU', year = 2020
            Returns: {'totalCount': 2463, 'items': [{'ELI': 'DU/2020/1', 'title': '...'}], 'count': 2463}
        User asks: "What acts were published in Monitor Polski in 2023?":
            Parameters: publisher = 'MP', year = 2023
        User asks: "List all legal acts from Dziennik Ustaw for 2019":
            Parameters: publisher = 'DU', year = 2019
        User asks: "How many acts were there in DU for 2022?":
            Parameters: publisher = 'DU', year = 2022
        User asks: "Browse acts from MP published in 2024":
            Parameters: publisher = 'MP', year = 2024
    """
    try:
        url = f"https://api.sejm.gov.pl/eli/acts/{publisher}/{year}"
        response = requests.get(url, headers={"Accept": "application/json"})

        if response.status_code == 200:
            return response.json()
        else:
            return {"totalCount": 0, "items": [], "count": 0}
    except Exception as e:
        print(f"Error: {e}")
        return {"totalCount": 0, "items": [], "count": 0}

# ========================================
# ACT DETAILS AND ANALYSIS
# ========================================

@app.tool(
    name="get_act_comprehensive_details",
    description="Get complete detailed information about a specific legal act including metadata, status, dates, and references.",
    tags={"analysis", "details", "act-info", "legal-research"}
)
def get_act_details(
    publisher: Annotated[str, "Publisher code (DU for Dziennik Ustaw, MP for Monitor Polski)"],
    year: Annotated[int, "Publication year"],
    num: Annotated[Union[int, str], "Act number/position within the year"]
) -> dict:
    """
    Fetches detailed information about a specific legal act from the Sejm API.

    Parameters:
        publisher (str): Publication code (e.g., 'DU' for Dziennik Ustaw)
        year (int): Year of publication
        num (int): Act number/position

    Returns:
        dict: Detailed information about the legal act, or empty dict if not found

    Examples:
        User asks: "Get details for DU/2020/1280":
            Parameters: publisher = 'DU', year = 2020, num = 1280
            Returns: {'ELI': 'DU/2020/1280', 'title': '...', 'type': 'Obwieszczenie', 'inForce': 'NOT_IN_FORCE', ...}
        User asks: "Show me information about act MP/2023/45":
            Parameters: publisher = 'MP', year = 2023, num = 45
        User asks: "What is the status of DU/2019/100?":
            Parameters: publisher = 'DU', year = 2019, num = 100
        User asks: "Give me full details of act DU/2022/500":
            Parameters: publisher = 'DU', year = 2022, num = 500
        User asks: "Tell me about the legal act DU/2021/250":
            Parameters: publisher = 'DU', year = 2021, num = 250
    """
    try:
        url = f"https://api.sejm.gov.pl/eli/acts/{publisher}/{year}/{num}"
        response = requests.get(url, headers={"Accept": "application/json"})

        if response.status_code == 200:
            return response.json()
        else:
            return {}
    except Exception as e:
        print(f"Error: {e}")
        return {}

@app.tool(
    name="get_act_content",
    description="Retrieve the actual text content of a legal act in PDF or HTML format. Use for reading the full document content.",
    tags={"analysis", "content", "text", "reading"}
)
def get_act_text(
    publisher: Annotated[str, "Publisher code (DU for Dziennik Ustaw, MP for Monitor Polski)"],
    year: Annotated[int, "Publication year"],
    num: Annotated[Union[int, str], "Act number/position within the year"],
    format_type: Annotated[str, "Content format: 'pdf' or 'html' (default: pdf)"] = "pdf"
) -> str:
    """
    Fetches the text content of a specific legal act in PDF or HTML format.

    Parameters:
        publisher (str): Publication code (e.g., 'DU' for Dziennik Ustaw)
        year (int): Year of publication
        num (int): Act number/position
        format_type (str, optional): Format type - 'pdf' or 'html'. Defaults to 'pdf'.

    Returns:
        str: Text content of the act, or empty string if not found.

    Examples:
        User asks: "Get the PDF text of DU/2020/1280":
            Parameters: publisher = 'DU', year = 2020, num = 1280, format_type = 'pdf'
            Returns: "PDF content available at: https://api.sejm.gov.pl/eli/acts/DU/2020/1280/text.pdf"
        User asks: "Get the HTML text of DU/2020/1":
            Parameters: publisher = 'DU', year = 2020, num = 1, format_type = 'html'
        User asks: "Download PDF of act MP/2023/100":
            Parameters: publisher = 'MP', year = 2023, num = 100, format_type = 'pdf'
        User asks: "Show me the HTML content of DU/2019/50":
            Parameters: publisher = 'DU', year = 2019, num = 50, format_type = 'html'
        User asks: "I need the text of act DU/2022/200 in PDF":
            Parameters: publisher = 'DU', year = 2022, num = 200, format_type = 'pdf'
    """
    try:
        url = f"https://api.sejm.gov.pl/eli/acts/{publisher}/{year}/{num}/text.{format_type}"
        response = requests.get(url)

        if response.status_code == 200:
            if format_type == "pdf":
                return f"PDF content available at: {url}"
            else:
                return response.text
        else:
            return ""
    except Exception as e:
        print(f"Error: {e}")
        return ""

@app.tool(
    name="get_act_table_of_contents",
    description="Get the hierarchical structure and table of contents of a legal act. Shows chapters, articles, sections, and their organization.",
    tags={"analysis", "structure", "navigation", "toc"}
)
def get_act_structure(
    publisher: Annotated[str, "Publisher code (DU for Dziennik Ustaw, MP for Monitor Polski)"],
    year: Annotated[int, "Publication year"],
    num: Annotated[Union[int, str], "Act number/position within the year"]
) -> list:
    """
    Fetches the structure/table of contents of a specific legal act.

    Parameters:
        publisher (str): Publication code (e.g., 'DU' for Dziennik Ustaw)
        year (int): Year of publication
        num (int): Act number/position

    Returns:
        list: List of structure elements (parts, chapters, articles, etc.)

    Examples:
        User asks: "Show me the structure of DU/2020/1":
            Parameters: publisher = 'DU', year = 2020, num = 1
            Returns: [{'id': 'part_1', 'title': 'Treść rozporządzenia', 'type': 'part', 'children': [...]}]
        User asks: "What is the table of contents for act MP/2023/50?":
            Parameters: publisher = 'MP', year = 2023, num = 50
        User asks: "Display the structure of DU/2019/100":
            Parameters: publisher = 'DU', year = 2019, num = 100
        User asks: "How is act DU/2022/75 organized?":
            Parameters: publisher = 'DU', year = 2022, num = 75
        User asks: "Give me the outline of legal act DU/2021/30":
            Parameters: publisher = 'DU', year = 2021, num = 30
    """
    try:
        url = f"https://api.sejm.gov.pl/eli/acts/{publisher}/{year}/{num}/struct"
        response = requests.get(url, headers={"Accept": "application/json"})

        if response.status_code == 200:
            return response.json()
        else:
            return []
    except Exception as e:
        print(f"Error: {e}")
        return []

@app.tool(
    name="get_act_relationships",
    description="Analyze legal relationships and references for an act. Shows which acts it amends, references, or is referenced by.",
    tags={"analysis", "references", "relationships", "legal-network"}
)
def get_act_references(
    publisher: Annotated[str, "Publisher code (DU for Dziennik Ustaw, MP for Monitor Polski)"],
    year: Annotated[int, "Publication year"],
    num: Annotated[Union[int, str], "Act number/position within the year"]
) -> dict:
    """
    Fetches references to/from a specific legal act (acts that reference this act or are referenced by this act).

    Parameters:
        publisher (str): Publication code (e.g., 'DU' for Dziennik Ustaw)
        year (int): Year of publication
        num (int): Act number/position

    Returns:
        dict: Dictionary of reference types with arrays of referenced acts.

    Examples:
        User asks: "What acts reference DU/2020/1?":
            Parameters: publisher = 'DU', year = 2020, num = 1
            Returns: {'Akty zmienione': [{'act': {'ELI': 'DU/2016/1498', ...}, 'date': '2020-01-17'}], ...}
        User asks: "Show me references for act MP/2023/25":
            Parameters: publisher = 'MP', year = 2023, num = 25
        User asks: "What laws does DU/2019/50 reference?":
            Parameters: publisher = 'DU', year = 2019, num = 50
        User asks: "Find acts that amended DU/2022/100":
            Parameters: publisher = 'DU', year = 2022, num = 100
        User asks: "What is the legal basis for act DU/2021/75?":
            Parameters: publisher = 'DU', year = 2021, num = 75
    """
    try:
        url = f"https://api.sejm.gov.pl/eli/acts/{publisher}/{year}/{num}/references"
        response = requests.get(url, headers={"Accept": "application/json"})

        if response.status_code == 200:
            return response.json()
        else:
            return {}
    except Exception as e:
        print(f"Error: {e}")
        return {}

@app.tool(
    name="get_legal_statuses",
    description="Get all possible legal act statuses (active, repealed, consolidated, etc.) for document classification and filtering.",
    tags={"metadata", "statuses", "reference", "legal-analysis"}
)
def get_statuses_list() -> list[str]:
    """
    Fetches a list of all possible legal act statuses.

    Returns:
        list: List of status strings.

    Examples:
        User asks: "What are the possible act statuses?":
            Returns: ['akt indywidualny', 'akt jednorazowy', 'akt objęty tekstem jednolitym', ...]
        User asks: "Show me all legal act status types":
            Returns: ['obowiązujący', 'uchylony', 'wygaśnięcie aktu', ...]
        User asks: "What statuses can a Polish law have?":
            Returns: ['akt obowiązujący', 'akt uchylony', 'tekst jednolity', ...]
        User asks: "List all possible statuses for legal documents":
            Returns: ['aktywny', 'nieaktywny', 'zmieniony', ...]
        User asks: "What are the different states a law can be in?":
            Returns: ['obowiązujący', 'uchylony', 'wygaśnięty', ...]
    """
    try:
        url = "https://api.sejm.gov.pl/eli/statuses"
        response = requests.get(url, headers={"Accept": "application/json"})

        if response.status_code == 200:
            return response.json()
        else:
            return []
    except Exception as e:
        print(f"Error: {e}")
        return []

@app.tool(
    name="get_legal_types",
    description="Retrieve all document types (laws, regulations, ordinances, etc.) used in Polish legal system.",
    tags={"metadata", "types", "reference", "legal-analysis"}
)
def get_types_list() -> list[str]:
    """
    Fetches a list of all possible legal act document types.

    Returns:
        list: List of document type strings.

    Examples:
        User asks: "What types of legal acts exist?":
            Returns: ['Ustawa', 'Rozporządzenie', 'Obwieszczenie', 'Zarządzenie', ...]
        User asks: "Show me all document types available":
            Returns: ['ustawa', 'rozporządzenie', 'obwieszczenie', 'zarządzenie', ...]
        User asks: "What kinds of legal documents are there?":
            Returns: ['akt normatywny', 'akt indywidualny', 'akt prawa miejscowego', ...]
        User asks: "List all types of Polish legal acts":
            Returns: ['konstytucja', 'ustawa', 'rozporządzenie', 'uchwała', ...]
        User asks: "What categories of laws exist in Poland?":
            Returns: ['akty normatywne', 'akty indywidualne', 'akty prawa wewnętrznego', ...]
    """
    try:
        url = "https://api.sejm.gov.pl/eli/types"
        response = requests.get(url, headers={"Accept": "application/json"})

        if response.status_code == 200:
            return response.json()
        else:
            return []
    except Exception as e:
        print(f"Error: {e}")
        return []

@app.tool(
    name="get_legal_institutions",
    description="Get list of all institutions involved in Polish legal acts (ministries, authorities, organizations that issue or are affected by laws).",
    tags={"metadata", "institutions", "reference", "legal-analysis"}
)
def get_institutions_list() -> list[str]:
    """
    Fetches a list of all institutions involved in legal acts.

    Returns:
        list: List of institution names.

    Examples:
        User asks: "What institutions are involved in legal acts?":
            Returns: ['MIN. SPRAWIEDLIWOŚCI', 'MIN. FINANSÓW', 'MIN. ZDROWIA', 'SEJM', ...]
        User asks: "Show me all institutions that create laws":
            Returns: ['Prezydent', 'Rada Ministrów', 'Ministerstwa', 'Sejm', ...]
        User asks: "What organizations issue legal documents?":
            Returns: ['MIN. EDUKACJI NARODOWEJ', 'MIN. OBRONY NARODOWEJ', 'NBP', ...]
        User asks: "List all authorities involved in Polish legislation":
            Returns: ['Sejm RP', 'Senat RP', 'Prezydent RP', 'Rada Ministrów', ...]
        User asks: "What bodies can pass laws in Poland?":
            Returns: ['PARLAMENT', 'PREZYDENT', 'RADA MINISTRÓW', 'MINISTERSTWA', ...]
    """
    try:
        url = "https://api.sejm.gov.pl/eli/institutions"
        response = requests.get(url, headers={"Accept": "application/json"})

        if response.status_code == 200:
            return response.json()
        else:
            return []
    except Exception as e:
        print(f"Error: {e}")
        return []


def main():
    app.run()

if __name__ == "__main__":
    main()