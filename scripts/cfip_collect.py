import os
import re
import sys
import shutil
import ipaddress
import requests
from collections import defaultdict


# ============================================================
# 配置
# ============================================================

API_URL = "https://cfip.118889.xyz/api/public_data"

# 网站当前页面默认延迟筛选
MAX_LATENCY = 130

OUTPUT_DIR = "output"

DEFAULT_PORT = 443

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/127.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://cfip.118889.xyz/",
}


# ============================================================
# 文件名安全处理
# ============================================================

def safe_filename(name):
    if not name:
        return "未知地区"

    name = str(name).strip()

    # 防止特殊字符破坏文件路径
    name = re.sub(r'[\\/:*?"<>|]', "_", name)

    name = name.strip(" .")

    return name or "未知地区"


# ============================================================
# 清理输出目录
# ============================================================

def prepare_output():

    if os.path.exists(OUTPUT_DIR):
        shutil.rmtree(OUTPUT_DIR)

    os.makedirs(
        os.path.join(OUTPUT_DIR, "all"),
        exist_ok=True
    )

    os.makedirs(
        os.path.join(OUTPUT_DIR, "node_location"),
        exist_ok=True
    )


# ============================================================
# 获取 API 数据
# ============================================================

def fetch_data():

    url = f"{API_URL}?lat={MAX_LATENCY}"

    print("==========================================")
    print("开始抓取 CFIP API")
    print("==========================================")
    print(f"URL: {url}")
    print()

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=60
    )

    print(f"HTTP 状态码: {response.status_code}")
    print(f"响应大小: {len(response.content)} bytes")

    response.raise_for_status()

    try:
        data = response.json()
    except Exception as e:
        print("API 返回内容不是有效 JSON")
        print(response.text[:2000])
        raise e

    if not isinstance(data, list):
        raise RuntimeError(
            f"API 返回类型异常: {type(data).__name__}"
        )

    print(f"API 原始节点数量: {len(data)}")

    return data


# ============================================================
# 判断纯 IP
# ============================================================

def parse_ip(value):
    """
    判断一个字符串是否为纯 IPv4 / IPv6。
    """

    if not value:
        return None

    value = str(value).strip()

    try:
        ip = ipaddress.ip_address(value)

        if ip.version == 4:
            return "ipv4", str(ip)

        if ip.version == 6:
            return "ipv6", str(ip)

    except ValueError:
        return None

    return None


# ============================================================
# 解析 IP / IP:PORT
# ============================================================

def parse_node_address(value):
    """
    支持：

    IPv4:
        1.2.3.4
        1.2.3.4:8443

    IPv6:
        2606:4700::1
        [2606:4700::1]:8443

    返回：

        family
        ip
        port
        formatted
    """

    if value is None:
        return None

    value = str(value).strip()

    if not value:
        return None

    # --------------------------------------------------------
    # 情况 1：IPv6 [IP]:PORT
    # --------------------------------------------------------

    if value.startswith("["):

        match = re.fullmatch(
            r"\[([0-9A-Fa-f:.]+)\](?::(\d+))?",
            value
        )

        if not match:
            return None

        ip_text = match.group(1)
        port_text = match.group(2)

        try:
            ip = ipaddress.IPv6Address(ip_text)
        except ValueError:
            return None

        port = (
            int(port_text)
            if port_text
            else DEFAULT_PORT
        )

        if not 1 <= port <= 65535:
            return None

        return {
            "family": "ipv6",
            "ip": str(ip),
            "port": port,
            "formatted": f"[{ip}]:{port}",
        }

    # --------------------------------------------------------
    # 情况 2：纯 IPv4
    # --------------------------------------------------------

    try:
        ip = ipaddress.IPv4Address(value)

        return {
            "family": "ipv4",
            "ip": str(ip),
            "port": DEFAULT_PORT,
            "formatted": f"{ip}:{DEFAULT_PORT}",
        }

    except ValueError:
        pass

    # --------------------------------------------------------
    # 情况 3：IPv4:PORT
    # --------------------------------------------------------

    match = re.fullmatch(
        r"([0-9.]+):(\d+)",
        value
    )

    if match:

        ip_text = match.group(1)
        port_text = match.group(2)

        try:
            ip = ipaddress.IPv4Address(ip_text)
            port = int(port_text)
        except ValueError:
            return None

        if not 1 <= port <= 65535:
            return None

        return {
            "family": "ipv4",
            "ip": str(ip),
            "port": port,
            "formatted": f"{ip}:{port}",
        }

    # --------------------------------------------------------
    # 情况 4：纯 IPv6
    # --------------------------------------------------------

    try:
        ip = ipaddress.IPv6Address(value)

        return {
            "family": "ipv6",
            "ip": str(ip),
            "port": DEFAULT_PORT,
            "formatted": f"[{ip}]:{DEFAULT_PORT}",
        }

    except ValueError:
        pass

    # --------------------------------------------------------
    # 情况 5：裸 IPv6 + 端口
    #
    # 某些数据源可能返回：
    #
    # 2606:4700::1:8443
    #
    # 这种格式存在歧义。
    #
    # 不强行猜测，避免错误拆分。
    # --------------------------------------------------------

    return None


# ============================================================
# 节点地址排序
# ============================================================

def address_sort_key(address):

    parsed = parse_node_address(address)

    if not parsed:
        return (99, "", 0)

    family_order = (
        0 if parsed["family"] == "ipv4"
        else 1
    )

    return (
        family_order,
        parsed["ip"],
        parsed["port"]
    )


# ============================================================
# 写文件
# ============================================================

def write_addresses(path, addresses):

    os.makedirs(
        os.path.dirname(path),
        exist_ok=True
    )

    addresses = sorted(
        addresses,
        key=address_sort_key
    )

    with open(
        path,
        "w",
        encoding="utf-8"
    ) as f:

        for address in addresses:
            f.write(address + "\n")


# ============================================================
# 处理节点
# ============================================================

def process_nodes(data):

    # --------------------------------------------------------
    # 所有 IPv4 / IPv6
    # --------------------------------------------------------

    all_ipv4 = set()
    all_ipv6 = set()

    # --------------------------------------------------------
    # 地区：
    #
    # node_location
    #     ↓
    # ipv4
    # ipv6
    # --------------------------------------------------------

    location_ipv4 = defaultdict(set)
    location_ipv6 = defaultdict(set)

    # --------------------------------------------------------
    # 统计
    # --------------------------------------------------------

    raw_count = len(data)

    valid_count = 0
    invalid_count = 0

    # ========================================================
    # 遍历 API 数据
    # ========================================================

    for item in data:

        if not isinstance(item, dict):
            invalid_count += 1
            continue

        node_ip = item.get("node_ip")

        if not node_ip:
            invalid_count += 1
            continue

        parsed = parse_node_address(node_ip)

        if not parsed:
            print(
                f"跳过无法解析的地址: {node_ip}"
            )

            invalid_count += 1
            continue

        valid_count += 1

        family = parsed["family"]

        formatted = parsed["formatted"]

        location = safe_filename(
            item.get(
                "node_location",
                "未知地区"
            )
        )

        # ====================================================
        # IPv4
        # ====================================================

        if family == "ipv4":

            all_ipv4.add(formatted)

            location_ipv4[
                location
            ].add(formatted)

        # ====================================================
        # IPv6
        # ====================================================

        elif family == "ipv6":

            all_ipv6.add(formatted)

            location_ipv6[
                location
            ].add(formatted)

    # ========================================================
    # all/ipv4.txt
    # ========================================================

    write_addresses(
        os.path.join(
            OUTPUT_DIR,
            "all",
            "ipv4.txt"
        ),
        all_ipv4
    )

    # ========================================================
    # all/ipv6.txt
    # ========================================================

    write_addresses(
        os.path.join(
            OUTPUT_DIR,
            "all",
            "ipv6.txt"
        ),
        all_ipv6
    )

    # ========================================================
    # 各地区 IPv4
    # ========================================================

    all_locations = (
        set(location_ipv4.keys())
        |
        set(location_ipv6.keys())
    )

    for location in sorted(all_locations):

        location_dir = os.path.join(
            OUTPUT_DIR,
            "node_location",
            location
        )

        os.makedirs(
            location_dir,
            exist_ok=True
        )

        # ----------------------------------------------------
        # IPv4
        # ----------------------------------------------------

        write_addresses(
            os.path.join(
                location_dir,
                "ipv4.txt"
            ),
            location_ipv4.get(
                location,
                set()
            )
        )

        # ----------------------------------------------------
        # IPv6
        # ----------------------------------------------------

        write_addresses(
            os.path.join(
                location_dir,
                "ipv6.txt"
            ),
            location_ipv6.get(
                location,
                set()
            )
        )

    # ========================================================
    # README
    # ========================================================

    write_readme(
        raw_count=raw_count,
        valid_count=valid_count,
        invalid_count=invalid_count,
        all_ipv4=all_ipv4,
        all_ipv6=all_ipv6,
        location_ipv4=location_ipv4,
        location_ipv6=location_ipv6,
    )


# ============================================================
# README
# ============================================================

def write_readme(
    raw_count,
    valid_count,
    invalid_count,
    all_ipv4,
    all_ipv6,
    location_ipv4,
    location_ipv6
):

    path = os.path.join(
        OUTPUT_DIR,
        "README.txt"
    )

    with open(
        path,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(
            "CFIP 自动抓取结果\n"
        )

        f.write(
            "==========================================\n\n"
        )

        f.write(
            f"API: {API_URL}?lat={MAX_LATENCY}\n"
        )

        f.write(
            f"原始节点: {raw_count}\n"
        )

        f.write(
            f"有效节点: {valid_count}\n"
        )

        f.write(
            f"无效节点: {invalid_count}\n\n"
        )

        f.write(
            f"IPv4: {len(all_ipv4)}\n"
        )

        f.write(
            f"IPv6: {len(all_ipv6)}\n\n"
        )

        f.write(
            "端口规则:\n"
        )

        f.write(
            "IPv4 无端口自动使用 :443\n"
        )

        f.write(
            "IPv6 无端口自动使用 [IP]:443\n"
        )

        f.write(
            "已有端口保持原端口\n\n"
        )

        f.write(
            "地区统计:\n"
        )

        f.write(
            "------------------------------------------\n"
        )

        locations = (
            set(location_ipv4.keys())
            |
            set(location_ipv6.keys())
        )

        for location in sorted(locations):

            ipv4_count = len(
                location_ipv4.get(
                    location,
                    set()
                )
            )

            ipv6_count = len(
                location_ipv6.get(
                    location,
                    set()
                )
            )

            f.write(
                f"{location}: "
                f"IPv4={ipv4_count}, "
                f"IPv6={ipv6_count}\n"
            )


# ============================================================
# 主程序
# ============================================================

def main():

    try:

        prepare_output()

        data = fetch_data()

        if not data:
            raise RuntimeError(
                "API 没有返回任何节点"
            )

        process_nodes(data)

        print()
        print("==========================================")
        print("抓取完成")
        print("==========================================")

    except Exception as e:

        print()
        print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        print("执行失败")
        print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        print(
            f"{type(e).__name__}: {e}"
        )
        print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")

        sys.exit(1)


if __name__ == "__main__":
    main()
