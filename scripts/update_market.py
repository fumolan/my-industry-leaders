#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股市场地图 · 每日更新脚本
==========================
采集: 全市场代码表(上市/退市/改名diff) + 90行业指数涨跌(排名变化)
     + 行业温度(成分股涨跌家数) + 大盘指数 + 涨跌停池
输出: market.json(当日快照) + history/YYYY-MM-DD.json(存档, 用于diff与轮动)
"""
import os, sys, io, json, time, urllib.request as ur
from datetime import datetime, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY_FILE = os.path.expanduser("~/claude/apikey/tonghuashun.txt")
FUYAO = "https://fuyao.aicubes.cn"

def fuyao(path):
    key = open(KEY_FILE).read().strip()
    req = ur.Request(FUYAO + path)
    req.add_header("X-api-key", key)
    return json.loads(ur.urlopen(req, timeout=20).read())

def tencent_batch(codes):
    """批量行情: {code: {name, price, chg}}"""
    out = {}
    for i in range(0, len(codes), 40):
        pre = ",".join(("bj" if c.startswith("92") else "sh" if c.startswith(("6","9")) else "sz")+c
                       for c in codes[i:i+40])
        try:
            req = ur.Request("https://qt.gtimg.cn/q="+pre)
            req.add_header("User-Agent","Mozilla/5.0")
            d = ur.urlopen(req, timeout=15).read().decode("gbk")
            for line in d.strip().split(";"):
                line = line.strip()
                if '"' not in line or '=' not in line: continue
                v = line.split('"')[1].split("~")
                if len(v) < 47 or not v[2]: continue
                try:
                    out[v[2]] = {"name": v[1].replace(" ",""), "price": float(v[3] or 0),
                                 "chg": float(v[32] or 0)}
                except Exception: pass
        except Exception as e:
            print(f"  [WARN] 行情批次失败: {str(e)[:50]}")
        time.sleep(0.18)
    return out

def main():
    today = datetime.now().strftime("%Y-%m-%d")
    hist_dir = os.path.join(REPO, "history")
    os.makedirs(hist_dir, exist_ok=True)
    print(f"=== 市场地图更新 {today} ===")

    # ── ① 全市场代码表 ──
    all_stocks, offset = {}, 0
    while True:
        d = fuyao(f"/api/meta/tickers/list?asset_type=a-share&limit=1000&offset={offset}")
        items = d.get("data",{}).get("item",[])
        if not items: break
        for it in items:
            all_stocks[it["ticker"]] = it.get("name","")
        if len(items) < 1000: break
        offset += 1000
        time.sleep(0.2)
    print(f"全市场代码表: {len(all_stocks)}只")

    # ── ② 与昨日快照 diff: 上市/退市/改名 ──
    snaps = sorted(f for f in os.listdir(hist_dir) if f.endswith(".json"))
    prev_date, prev_stocks, prev_ind = None, None, None
    if snaps:
        prev = json.load(open(os.path.join(hist_dir, snaps[-1])))
        prev_date = prev.get("date")
        prev_stocks = prev.get("all_stocks", {})
        prev_ind = {r["name"]: r for r in prev.get("industries", [])}

    new_listings, delistings, renames = [], [], []
    if prev_stocks:
        for t in all_stocks:
            if t not in prev_stocks:
                new_listings.append({"code": t, "name": all_stocks[t]})
        for t, n in prev_stocks.items():
            if t not in all_stocks:
                delistings.append({"code": t, "name": n})
            elif n != all_stocks[t]:
                renames.append({"code": t, "old": n, "new": all_stocks[t]})
    print(f"对比{prev_date or '(首日基线)'}: 新上市{len(new_listings)} 退市{len(delistings)} 改名{len(renames)}")

    # ── ③ 90行业指数涨跌 + 排名变化 ──
    d = fuyao("/api/a-share-index/catalog/ths-index-list?tag=industry")
    inds = {it["thscode"]: it["name"] for it in d["data"]["item"] if it["thscode"].startswith("881")}
    codes = list(inds)
    industries = []
    for i in range(0, len(codes), 45):
        d2 = fuyao(f"/api/a-share-index/prices/snapshot?thscodes={','.join(codes[i:i+45])}")
        for it in d2.get("data",{}).get("item",[]):
            industries.append({"name": inds.get(it["thscode"], it["thscode"]),
                               "chg": round(it.get("price_change_ratio_pct",0),2)})
        time.sleep(0.2)
    industries.sort(key=lambda x: -x["chg"])
    for rank, ind in enumerate(industries, 1):
        ind["rank"] = rank
        if prev_ind and ind["name"] in prev_ind:
            ind["rank_chg"] = prev_ind[ind["name"]]["rank"] - rank   # 正=上升
            ind["prev_chg"] = prev_ind[ind["name"]]["chg"]
        else:
            ind["rank_chg"] = None
            ind["prev_chg"] = None
    print(f"行业指数: {len(industries)}个 | 今日领涨: {industries[0]['name']}{industries[0]['chg']:+.2f}% "
          f"领跌: {industries[-1]['name']}{industries[-1]['chg']:+.2f}%")

    # ── ④ 行业温度(成分股上涨占比) + 涨跌家数 ──
    members = json.load(open("/tmp/industry_members.json")) if os.path.exists("/tmp/industry_members.json") else None
    up_count = down_count = flat = 0
    if members is None:
        # 重建成分映射
        members = {"inds": inds, "members": {}}
        for i, (code, name) in enumerate(inds.items(), 1):
            try:
                d3 = fuyao(f"/api/a-share-index/constituents/ths-stock-list?thscode={code}")
                members["members"][code] = [it["ticker"] for it in d3.get("data",{}).get("item",[])]
            except Exception: members["members"][code] = []
            time.sleep(0.1)
        json.dump(members, open("/tmp/industry_members.json","w"), ensure_ascii=False)
    mem = members["members"]
    all_tickers = sorted(set(t for v in mem.values() for t in v))
    quotes = tencent_batch(all_tickers)
    print(f"成分股行情: {len(quotes)}只")
    for q in quotes.values():
        if q["chg"] > 0: up_count += 1
        elif q["chg"] < 0: down_count += 1
        else: flat += 1
    for ind in industries:
        tickers = mem.get([k for k,v in inds.items() if v==ind["name"]][0], [])
        ups = sum(1 for t in tickers if t in quotes and quotes[t]["chg"] > 0)
        total = sum(1 for t in tickers if t in quotes)
        ind["up_ratio"] = round(ups/total*100) if total else 0

    # ── ⑤ 大盘指数 + 涨跌停池 ──
    IDX = {"000001.SH":"上证指数","399001.SZ":"深证成指","399006.SZ":"创业板指","000300.SH":"沪深300"}
    d = fuyao(f"/api/a-share-index/prices/snapshot?thscodes={','.join(IDX)}")
    indices = [{"name": IDX[it["thscode"]], "close": it["last_price"],
                "chg": round(it.get("price_change_ratio_pct",0),2)}
               for it in d.get("data",{}).get("item",[])]
    try:
        zt = fuyao("/api/a-share/special-data/limit-up-pool").get("pagination",{}).get("total",0)
        dt = fuyao("/api/a-share/special-data/limit-down-pool").get("pagination",{}).get("total",0)
    except Exception:
        zt = dt = None
    print(f"指数: {[(x['name'], x['chg']) for x in indices]} | 涨停{zt} 跌停{dt} | 涨{up_count} 跌{down_count}")

    # ── ⑥ 组装输出 ──
    market = {
        "date": today, "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "indices": indices,
        "breadth": {"up": up_count, "down": down_count, "flat": flat},
        "limit": {"up": zt, "down": dt},
        "ipo_delist": {"new": new_listings[:30], "new_count": len(new_listings),
                       "delist": delistings[:30], "delist_count": len(delistings),
                       "renames": renames[:30], "rename_count": len(renames),
                       "prev_date": prev_date},
        "industries": industries,
        "all_stocks": all_stocks,
    }
    with open(os.path.join(REPO, "market.json"), "w") as f:
        json.dump(market, f, ensure_ascii=False)
    # 存档(含全代码表, 供次日diff; 网页只用 market.json)
    with open(os.path.join(hist_dir, f"{today}.json"), "w") as f:
        json.dump(market, f, ensure_ascii=False)
    # 清理30天前存档
    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    for f_ in snaps:
        if f_[:10] < cutoff:
            os.remove(os.path.join(hist_dir, f_))
    print(f"✅ market.json + history/{today}.json 已生成")

if __name__ == "__main__":
    main()
