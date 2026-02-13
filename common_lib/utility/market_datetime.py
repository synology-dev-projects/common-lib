import pandas_market_calendars as mcal
import datetime

def convert_to_valid_market_date_range(start_date: str, end_date: str) -> (str, str):
    """
    Checks start and end dates of a date range
     and adjust its forward or back if they are not on a valid market day (includng holidays)
    :param start_date:
    :param end_date:
    :return:
    """

    #get list of valid market days
    nyse = mcal.get_calendar('NYSE')
    valid_days= nyse.valid_days(start_date=start_date, end_date=end_date)

    start_date_valid = valid_days.min().strftime('%Y-%m-%d')
    end_date_valid = valid_days.max().strftime('%Y-%m-%d')
    return start_date_valid, end_date_valid

def convert_to_valid_market_days(start_date: datetime, end_date: datetime) -> int:
    """
    Checks start and end dates of a date range
     and adjust its forward or back if they are not on a valid market day (includng holidays)
    :param start_date:
    :param end_date:
    :return:
    """

    #get list of valid market days
    nyse = mcal.get_calendar('NYSE')
    valid_days= nyse.valid_days(start_date=start_date, end_date=end_date)

    return len(valid_days)



def get_trading_day_count(start_str:str, end_str:str, exchange='NYSE'):
    """
    Returns the number of valid trading days between two dates (inclusive).

    :param start_str: 'YYYY-MM-DD' (e.g., '2025-07-01')
    :param end_str:   'YYYY-MM-DD' (e.g., '2025-07-12')
    :param exchange:  'NYSE', 'NASDAQ', 'CBOE', etc.
    :return: int (Count of market days)
    """
    # 1. Load the calendar (e.g., NYSE or CBOE)
    cal = mcal.get_calendar(exchange)

    # 2. Get the schedule of open days
    # This automatically excludes weekends and holidays
    schedule = cal.schedule(start_date=start_str, end_date=end_str)

    # 3. Return the number of rows in the schedule
    return len(schedule)