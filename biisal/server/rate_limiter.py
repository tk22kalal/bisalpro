"""
Rate limiter for download requests to prevent Telegram timeout errors
"""
import time
from collections import defaultdict
from typing import Dict, Tuple


class RateLimiter:
    def __init__(self):
        # Track requests per IP: {ip: [(timestamp, count), ...]}
        self.ip_requests: Dict[str, list] = defaultdict(list)
        # Maximum requests per IP in time window
        self.max_requests_per_window = 2  # Max 2 concurrent downloads per IP
        self.time_window = 60  # 60 seconds window
        # Cooldown between requests from same IP
        self.min_delay_between_requests = 5  # 5 seconds between requests
        
    def clean_old_entries(self, ip: str):
        """Remove entries older than time window"""
        current_time = time.time()
        if ip in self.ip_requests:
            self.ip_requests[ip] = [
                (timestamp, count) for timestamp, count in self.ip_requests[ip]
                if current_time - timestamp < self.time_window
            ]
            
    def can_proceed(self, ip: str) -> Tuple[bool, str]:
        """
        Check if request can proceed
        Returns: (can_proceed: bool, message: str)
        """
        current_time = time.time()
        self.clean_old_entries(ip)
        
        # Check if there's a recent request from this IP
        if ip in self.ip_requests and self.ip_requests[ip]:
            last_request_time, _ = self.ip_requests[ip][-1]
            time_since_last = current_time - last_request_time
            
            # Enforce minimum delay between requests
            if time_since_last < self.min_delay_between_requests:
                wait_time = int(self.min_delay_between_requests - time_since_last)
                return False, f"Please wait {wait_time} seconds before next request"
        
        # Count active requests in current window
        active_count = len(self.ip_requests.get(ip, []))
        
        if active_count >= self.max_requests_per_window:
            return False, "Too many active downloads. Please wait for current downloads to finish."
        
        return True, "OK"
    
    def add_request(self, ip: str):
        """Record a new request"""
        current_time = time.time()
        self.ip_requests[ip].append((current_time, 1))
        
    def remove_request(self, ip: str):
        """Remove completed request"""
        if ip in self.ip_requests and self.ip_requests[ip]:
            self.ip_requests[ip].pop()
            

# Global rate limiter instance
rate_limiter = RateLimiter()
