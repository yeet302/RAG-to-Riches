"""Code Agent API; load Azure clients only when constructing the investigator."""
__all__ = ['CodeAgent']


def __getattr__(name):
    if name == 'CodeAgent':
        from .agent import CodeAgent
        return CodeAgent
    raise AttributeError(name)
