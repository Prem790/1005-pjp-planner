"""Shared, deterministic travel calculations matching the provided simulator."""
import math


def haversine_km(origin, destination):
    if any(not math.isfinite(float(x)) for x in (*origin,*destination)):
        raise ValueError('Coordinates must be finite')
    for latitude,longitude in [origin,destination]:
        if not -90<=latitude<=90 or not -180<=longitude<=180:
            raise ValueError('Coordinates are outside valid latitude/longitude ranges')
    a,b,c,d=map(math.radians,(*origin,*destination))
    h=math.sin((c-a)/2)**2+math.cos(a)*math.cos(c)*math.sin((d-b)/2)**2
    return 6371*2*math.asin(min(1,math.sqrt(h)))


def direct_travel_minutes(origin,destination,kind,model):
    if destination is None:
        return float(model['unknown_location_travel_minutes'][kind])
    return haversine_km(origin,destination)*model['road_factor']/model['speed_kmh'][kind]*60


def normal_day_lower_bound(direct_minutes,visits,model):
    """Optimistic bound: direct travel plus visit service times, no other travel.

    Any day reaching this dealer needs at least the direct distance from its
    start. Passing this test does not establish a feasible multi-stop route.
    """
    return direct_minutes+visits*model['dealer_call_minutes']
