"""
Travel booking: flights, hotels, car hire and trains.

    base.py      provider interface every API integration implements
    mock.py      offline sandbox providers (default, no API keys needed)
    duffel.py    Duffel API: real flights and hotel stays (test mode is free)
    partner.py   generic REST adapter for a car/train partner API you have a contract with
    registry.py  picks a provider per kind from env vars (FLIGHT_PROVIDER=duffel, ...)
    store.py     SQLite: cached search offers + confirmed trips
    service.py   validation and booking rules (what travel_server.py calls)
"""
