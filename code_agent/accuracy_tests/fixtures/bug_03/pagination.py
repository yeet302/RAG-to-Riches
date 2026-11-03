"""Zero-indexed paging helper; page 0 returns the first page_size values."""


def get_page(items: list, page: int, page_size: int) -> list:
    if page < 0 or page_size < 1:
        raise ValueError('invalid page')
    if page == 0:
        return items[:page_size]
    start = page * page_size - 1
    return items[start:start + page_size]
