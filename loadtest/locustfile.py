"""Locust 压测:报表并发场景(k6-report.js 的等价 Python 版)。

前置条件与 k6 版本相同:本地演示环境 + DEMO_ENABLED=true。

运行:
    uv run --with locust locust -f loadtest/locustfile.py \
        --host http://127.0.0.1:8000 --users 50 --spawn-rate 5 --run-time 3m

高并发(>8 用户/分钟)时演示登录会被限流(每 IP 每分钟 10 次),两种方式任选:
    1. 降低 --spawn-rate,让登录分散;
    2. 预先手工登录一次,把 Cookie 传进来,完全跳过登录:
       LOCUST_SESSION_COOKIE="monitor_session=..." uv run --with locust locust -f ...

所有压测端点均为 GET,无需 CSRF 头。请勿对生产环境执行。
"""

import os
import random

from locust import HttpUser, between, task


class ReportViewer(HttpUser):
    wait_time = between(3, 8)

    def on_start(self):
        cookie = os.environ.get("LOCUST_SESSION_COOKIE", "")
        if cookie:
            self.client.headers["Cookie"] = cookie
            return
        response = self.client.post(
            "/api/v1/auth/demo",
            json={"password": os.environ.get("DEMO_PASSWORD", ""), "role": "admin"},
            headers={"Content-Type": "application/json"},
        )
        if response.status_code == 429:
            # 限流时退避后重试,避免并发启动阶段的登录风暴。
            response = self.client.post(
                "/api/v1/auth/demo",
                json={"password": os.environ.get("DEMO_PASSWORD", ""), "role": "admin"},
                headers={"Content-Type": "application/json"},
            )
        response.raise_for_status()
        cookie_value = response.cookies.get("monitor_session")
        if cookie_value:
            self.client.headers["Cookie"] = f"monitor_session={cookie_value}"

    @task(7)
    def dashboard(self):
        self.client.get("/api/v1/dashboard", name="/api/v1/dashboard")

    @task(2)
    def events(self):
        self.client.get("/api/v1/events?limit=20", name="/api/v1/events?limit=20")

    @task(1)
    def employees(self):
        self.client.get("/api/v1/employees?limit=50", name="/api/v1/employees?limit=50")


class SlowViewer(HttpUser):
    """与前端一致的 30 秒看板轮询画像,用于容量估算而非压力峰值。"""

    wait_time = between(28, 32)
    weight = 0

    def on_start(self):
        ReportViewer.on_start(self)

    @task
    def poll_dashboard(self):
        self.client.get("/api/v1/dashboard", name="/api/v1/dashboard")
        if random.random() < 0.3:
            self.client.get("/api/v1/events?limit=20", name="/api/v1/events?limit=20")
