class RateLimiter:
    def can_proceed(self, ip: str):
        return True, "OK"

    def add_request(self, ip: str):
        pass

    def remove_request(self, ip: str):
        pass


rate_limiter = RateLimiter()
