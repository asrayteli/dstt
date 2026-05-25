from __future__ import annotations


# 適用: 令和7年9月26日公示、令和7年11月1日まで順次適用の貸切バス新公示運賃。
# 出典: 国土交通省「貸切バスの新たな運賃・料金を公示します。」
BLOCK_MASTER: dict[str, dict] = {
    "hokkaido": {
        "name": "北海道運輸局",
        "util_rate": 0.7143,
        "company_util_rate": 0.7143,
        "dist_rate": {"large": 150, "medium": 130, "small": 110, "commuter": 100},
        "time_rate": {"large": 6080, "medium": 5130, "small": 4500, "commuter": 4010},
        "alt_dist_rate": 10,
        "alt_time_rate": 2410,
    },
    "tohoku": {
        "name": "東北運輸局",
        "util_rate": 0.5810,
        "company_util_rate": 0.5810,
        "dist_rate": {"large": 180, "medium": 160, "small": 140, "commuter": 120},
        "time_rate": {"large": 7130, "medium": 6020, "small": 5270, "commuter": 4700},
        "alt_dist_rate": 20,
        "alt_time_rate": 2400,
    },
    "kanto": {
        "name": "関東運輸局",
        "util_rate": 0.6758,
        "company_util_rate": 0.6758,
        "dist_rate": {"large": 170, "medium": 150, "small": 130, "commuter": 120},
        "time_rate": {"large": 7190, "medium": 6070, "small": 5320, "commuter": 4740},
        "alt_dist_rate": 40,
        "alt_time_rate": 2670,
    },
    "hokuriku_shinetsu": {
        "name": "北陸信越運輸局",
        "util_rate": 0.5833,
        "company_util_rate": 0.5833,
        "dist_rate": {"large": 160, "medium": 140, "small": 120, "commuter": 110},
        "time_rate": {"large": 7030, "medium": 5930, "small": 5190, "commuter": 4630},
        "alt_dist_rate": 20,
        "alt_time_rate": 2470,
    },
    "chubu": {
        "name": "中部運輸局",
        "util_rate": 0.6645,
        "company_util_rate": 0.6645,
        "dist_rate": {"large": 150, "medium": 130, "small": 110, "commuter": 100},
        "time_rate": {"large": 7430, "medium": 6270, "small": 5490, "commuter": 4900},
        "alt_dist_rate": 30,
        "alt_time_rate": 2610,
    },
    "kinki": {
        "name": "近畿運輸局",
        "util_rate": 0.5996,
        "company_util_rate": 0.5996,
        "dist_rate": {"large": 170, "medium": 140, "small": 120, "commuter": 110},
        "time_rate": {"large": 8040, "medium": 6790, "small": 5950, "commuter": 5300},
        "alt_dist_rate": 30,
        "alt_time_rate": 2480,
    },
    "chugoku": {
        "name": "中国運輸局",
        "util_rate": 0.5943,
        "company_util_rate": 0.5943,
        "dist_rate": {"large": 200, "medium": 170, "small": 150, "commuter": 130},
        "time_rate": {"large": 6890, "medium": 5820, "small": 5090, "commuter": 4540},
        "alt_dist_rate": 30,
        "alt_time_rate": 2460,
    },
    "shikoku": {
        "name": "四国運輸局",
        "util_rate": 0.5404,
        "company_util_rate": 0.5404,
        "dist_rate": {"large": 150, "medium": 130, "small": 110, "commuter": 100},
        "time_rate": {"large": 6940, "medium": 5860, "small": 5130, "commuter": 4570},
        "alt_dist_rate": 30,
        "alt_time_rate": 2420,
    },
    "kyushu": {
        "name": "九州運輸局",
        "util_rate": 0.6285,
        "company_util_rate": 0.6285,
        "dist_rate": {"large": 150, "medium": 130, "small": 120, "commuter": 100},
        "time_rate": {"large": 6920, "medium": 5840, "small": 5110, "commuter": 4560},
        "alt_dist_rate": 10,
        "alt_time_rate": 2430,
    },
    "okinawa": {
        "name": "沖縄総合事務局",
        "util_rate": 0.6278,
        "company_util_rate": 0.6278,
        "dist_rate": {"large": 210, "medium": 180, "small": 160, "commuter": 140},
        "time_rate": {"large": 5710, "medium": 4820, "small": 4220, "commuter": 3760},
        "alt_dist_rate": 30,
        "alt_time_rate": 2660,
    },
}


CAR_MASTER: dict[str, dict] = {
    "large": {"label": "大型車", "max_capacity": 60, "alt_seat_cost": 2, "alt_allowed": True},
    "medium": {"label": "中型車", "max_capacity": 27, "alt_seat_cost": 2, "alt_allowed": True},
    "small": {"label": "小型車", "max_capacity": 24, "alt_seat_cost": 0, "alt_allowed": False},
    "commuter": {"label": "コミューター", "max_capacity": 14, "alt_seat_cost": 0, "alt_allowed": False},
}
