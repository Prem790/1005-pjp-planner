"""Exact open-path travel optimization for at most six dealer stops.

Unknown locations add a fixed travel allowance without moving the known position,
matching the supplied simulator. No return leg is required by its contract.
"""
from functools import lru_cache
from .travel import direct_travel_minutes


class RouteSolver:
    def __init__(self, records, start, kind, model):
        self.records = records
        self.model = model
        self.start = start
        self.kind = kind
        self.position = {k: ((float(v['lat']), float(v['lon']))
                              if v['has_location'] else None) for k, v in records.items()}
        known = [k for k in records if self.position[k] is not None]
        self.initial = {k: direct_travel_minutes(start, self.position[k], kind, model) for k in known}
        self.distance = {(a,b): direct_travel_minutes(self.position[a], self.position[b], kind, model)
                         for a in known for b in known if a != b}
        self.solve = lru_cache(maxsize=100000)(self._solve)

    def priority(self, key):
        return float(self.records[key]['priority_score'])

    def _solve(self, keys):
        if len(keys) > 6 or len(set(keys)) != len(keys):
            raise ValueError('A route needs at most six distinct accounts')
        known = [k for k in keys if self.position[k] is not None]
        unknown = sorted((k for k in keys if self.position[k] is None), key=lambda k: (-self.priority(k), k))
        if not known:
            return len(unknown)*self.model['unknown_location_travel_minutes'][self.kind], tuple(unknown)
        # Held-Karp: shortest open path, with stable ID tie-breaking.
        dp = {(1 << j, j): (self.initial[k], (k,)) for j,k in enumerate(known)}
        for mask in range(1, 1 << len(known)):
            for last in range(len(known)):
                if (mask,last) not in dp:
                    continue
                value, path = dp[mask,last]
                for nxt,k in enumerate(known):
                    if mask & (1 << nxt):
                        continue
                    state = (mask | (1 << nxt), nxt)
                    candidate = (value+self.distance[known[last],k], path+(k,))
                    if state not in dp or candidate < dp[state]:
                        dp[state] = candidate
        value,path = min(dp[(1 << len(known))-1,j] for j in range(len(known)))
        # Merge unknown stops by priority; this leaves optimal travel unchanged.
        ordered = list(path)
        for k in unknown:
            index = next((i for i,p in enumerate(ordered) if self.priority(k)>self.priority(p)), len(ordered))
            ordered.insert(index,k)
        value += len(unknown)*self.model['unknown_location_travel_minutes'][self.kind]
        return value, tuple(ordered)

    def route(self, keys):
        return self.solve(tuple(sorted(keys)))

    def legs(self, keys):
        position = self.start
        result = []
        for k in keys:
            destination = self.position[k]
            result.append(direct_travel_minutes(position, destination, self.kind, self.model))
            if destination is not None:
                position = destination
        return result
