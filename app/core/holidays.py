import datetime

def easter_sunday(year: int) -> datetime.date:
    """Anonymous Gregorian algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return datetime.date(year, month, day)

def alla_helgons_dag(year: int) -> datetime.date:
    """Saturday between 31 Oct and 6 Nov."""
    d = datetime.date(year, 10, 31)
    while d.weekday() != 5:  # 5 = Saturday
        d += datetime.timedelta(days=1)
    return d

def midsommarafton(year: int) -> datetime.date:
    """Friday between 19 and 25 June."""
    d = datetime.date(year, 6, 19)
    while d.weekday() != 4:  # 4 = Friday
        d += datetime.timedelta(days=1)
    return d

def first_weekday_after(date_: datetime.date) -> datetime.date:
    """First Monday–Friday after given date."""
    d = date_ + datetime.timedelta(days=1)
    while d.weekday() >= 5:  # 5–6 = Saturday/Sunday
        d += datetime.timedelta(days=1)
    return d


def trettondagen(year: int) -> datetime.date:
    """Epiphany / January 6."""
    return datetime.date(year, 1, 6)


def forsta_maj(year: int) -> datetime.date:
    """May 1st (Labour Day)."""
    return datetime.date(year, 5, 1)


def nationaldagen(year: int) -> datetime.date:
    """Swedish National Day, June 6th."""
    return datetime.date(year, 6, 6)


def kristi_himmelsfardsdag(year: int) -> datetime.date:
    """Ascension Day: 39 days after Easter Sunday (Thursday)."""
    return easter_sunday(year) + datetime.timedelta(days=39)


def skartorsdagen(year: int) -> datetime.date:
    """Maundy Thursday: Thursday before Easter Sunday."""
    return easter_sunday(year) - datetime.timedelta(days=3)


def pingstafton(year: int) -> datetime.date:
    """Pentecost eve: Saturday before Pentecost (48 days after Easter)."""
    return easter_sunday(year) + datetime.timedelta(days=48)


def julafton(year: int) -> datetime.date:
    """Christmas Eve: December 24th."""
    return datetime.date(year, 12, 24)


def nyarsafton(year: int) -> datetime.date:
    """New Year's Eve: December 31st."""
    return datetime.date(year, 12, 31)


def nyarsdagen(year: int) -> datetime.date:
    """New Year's Day: January 1st."""
    return datetime.date(year, 1, 1)


def langfredagen(year: int) -> datetime.date:
    """Good Friday (Långfredagen): Friday before Easter Sunday."""
    return easter_sunday(year) - datetime.timedelta(days=2)
