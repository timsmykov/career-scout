"""One explicit factory; unavailable sources do not change another source's transport."""

from career_scout.adapters import HabrClient, SuperJobClient, TrudvsemClient
from career_scout.common import SourceError
from career_scout.hh import HHClient


def create_client(source, credentials, *, options=None, **kwargs):
    if source == "hh":
        return HHClient(credentials, **kwargs)
    if source == "superjob":
        return SuperJobClient(credentials, options=options, **kwargs)
    if source == "habr":
        return HabrClient(options=options, **kwargs)
    if source == "trudvsem":
        return TrudvsemClient(options=options, **kwargs)
    raise SourceError("source_adapter_unavailable")
