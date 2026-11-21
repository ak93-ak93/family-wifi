"""A fake UniFi OS console for tests."""

import json

import httpx

A, B, C = "aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02", "aa:bb:cc:dd:ee:03"


class FakeUnifi:
    """Just enough of a UniFi OS console to exercise the client."""

    def __init__(self):
        self.blocked = set()
        self.groups = [{"_id": "g-default", "name": "Default", "attr_no_delete": True,
                        "qos_rate_max_down": -1, "qos_rate_max_up": -1}]
        self.users = {m: {"_id": f"u-{m[-2:]}", "mac": m, "usergroup_id": "", "name": n}
                      for m, n in ((A, "Alex iPad"), (B, "Alex Switch"), (C, "Sam iPhone"))}
        self.rules = {}
        self.calls = []
        self.reject_login = False

    def __call__(self, req: httpx.Request) -> httpx.Response:
        p, m = req.url.path, req.method
        body = json.loads(req.content) if req.content else None
        self.calls.append((m, p, body))
        ok = lambda data: httpx.Response(200, json={"meta": {"rc": "ok"}, "data": data})
        if p == "/":
            return httpx.Response(200, text="unifi os")
        if p == "/api/auth/login":
            if self.reject_login:
                return httpx.Response(401, json={})
            return httpx.Response(200, json={}, headers={"x-csrf-token": "csrf"})
        assert req.headers.get("x-csrf-token") == "csrf"
        base, v2 = "/proxy/network/api/s/default", "/proxy/network/v2/api/site/default"
        if p == base + "/cmd/stamgr":
            if body["cmd"] == "block-sta":
                self.blocked.add(body["mac"])
            elif body["cmd"] == "unblock-sta":
                self.blocked.discard(body["mac"])
            return ok([])
        if p == base + "/stat/sta":
            return ok([{"mac": A}])
        if p.startswith(base + "/stat/user/"):
            u = self.users.get(p.rsplit("/", 1)[1])
            return ok([u] if u else [])
        if p == base + "/rest/usergroup" and m == "GET":
            return ok(self.groups)
        if p == base + "/rest/usergroup" and m == "POST":
            g = {"_id": f"g{len(self.groups)}", **body}
            self.groups.append(g)
            return ok([g])
        if p.startswith(base + "/rest/usergroup/"):
            return ok([body])
        if p == base + "/rest/user" and m == "GET":
            return ok(list(self.users.values()))
        if p.startswith(base + "/rest/user/"):
            uid = p.rsplit("/", 1)[1]
            next(u for u in self.users.values() if u["_id"] == uid)["usergroup_id"] = body["usergroup_id"]
            return ok([])
        if p == v2 + "/trafficrules" and m == "POST":
            rid = f"r{len(self.rules) + 1}"
            self.rules[rid] = {"_id": rid, **body}
            return httpx.Response(201, json=self.rules[rid])
        if p.startswith(v2 + "/trafficrules/") and m == "DELETE":
            self.rules.pop(p.rsplit("/", 1)[1], None)
            return httpx.Response(200, content=b"")
        return httpx.Response(404, json={"meta": {"rc": "error", "msg": "not found"}})
