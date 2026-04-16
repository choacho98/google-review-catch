import time
import pandas as pd
import csv
import random
import os
import re
from playwright.sync_api import sync_playwright

# =============================
# 随机 User-Agent
# =============================
def random_user_agent():
    ua_list = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    ]
    return random.choice(ua_list)

# =============================
# 读取CSV
# =============================
def read_places(csv_file):
    places = []
    if not os.path.exists(csv_file):
        print(f"❌ 找不到CSV文件: {csv_file}")
        return places
    with open(csv_file, 'r', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        try:
            next(reader) # 跳过表头
        except StopIteration:
            return []
        for row in reader:
            if len(row) >= 2:
                places.append((row[0], row[1]))
    return places

# =============================
# 抓评论（核心函数 - 已修复DOM卸载和重复问题）
# =============================
def scrape_reviews(place_name, url, max_scrolls=50):
    unique_reviews = {}  # 使用字典去重，键为 data-review-id
    print(f"\n🚀 开始抓取: {place_name}")

    # 强制在URL中加入 hl=en 或 hl=ja 参数，有助于显示所有语言的评论而不被隐藏
    if 'hl=' not in url:
        url += '&hl=ja' if '?' in url else '?hl=ja'

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,  
            args=['--disable-blink-features=AutomationControlled']
        )
        context = browser.new_context(
            user_agent=random_user_agent(),
            viewport={'width': 1280, 'height': 800},
            # 移除 locale 限制，避免 Google 强制过滤非本地语言评论
        )
        page = context.new_page()

        try:
            print(f"正在加载页面: {url}")
            page.goto(url, timeout=60000, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
            
            # 查找并点击评论按钮
            print("正在寻找评论按钮...")
            review_tab_selectors = [
                'button[role="tab"]:has-text("Reviews")',
                'button[role="tab"]:has-text("评价")',
                'button[role="tab"]:has-text("クチコミ")',
                'button[aria-label*="Reviews"]'
            ]
            
            clicked = False
            for selector in review_tab_selectors:
                try:
                    locator = page.locator(selector).first
                    if locator.is_visible(timeout=3000):
                        locator.click()
                        clicked = True
                        print("✅ 已成功进入评论面板")
                        page.wait_for_timeout(3000)
                        break
                except:
                    continue
            
            if not clicked:
                print("⚠️ 未找到常规评论按钮，尝试直接修改URL进入评论区...")
                try:
                    place_id_match = re.search(r'!1s([^!]+)', url)
                    if place_id_match:
                        place_id = place_id_match.group(1)
                        reviews_url = f"https://www.google.com/maps/place/data=!4m5!3m4!1s{place_id}!8m2!3d0!4d0"
                        page.goto(reviews_url, timeout=30000)
                        page.wait_for_timeout(5000)
                except:
                    pass

            # 找到可滚动的评论容器
            scroll_container = page.locator('div.m6QErb.DxyBCb.kA9KIf.dS8AEf, div[role="feed"]').first
            if not scroll_container.is_visible():
                print("❌ 未能定位到评论滚动容器")
                browser.close()
                return []

            print(f"⏬ 正在采用边滚动、边提取策略以防数据丢失...")
            
            consecutive_no_new_reviews = 0

            for i in range(max_scrolls):
                # 1. 获取当前 DOM 中挂载的评论区块
                current_elements = page.locator('div[data-review-id]').all()
                new_found_in_this_scroll = 0

                for r in current_elements:
                    rid = r.get_attribute('data-review-id')
                    
                    # 2. 如果是新的评论ID，则进行提取
                    if rid and rid not in unique_reviews:
                        try:
                            # 尝试点击“更多”按钮展开长评论
                            more_btn = r.locator('button:has-text("More"), button:has-text("更多"), button:has-text("もっと見る")').first
                            if more_btn.is_visible():
                                more_btn.click(timeout=1000)
                                page.wait_for_timeout(200) # 给文本展开一点时间

                            # 提取文本
                            text_elem = r.locator('.wiI7pd, .MyEned').first
                            text = text_elem.inner_text().strip() if text_elem.is_visible() else ""

                            # 如果有文字内容才保存
                            if text:
                                # 提取评分
                                rating = ""
                                rating_elem = r.locator('[role="img"][aria-label*="star"], [role="img"][aria-label*="星"]').first
                                if rating_elem.is_visible():
                                    aria_label = rating_elem.get_attribute("aria-label")
                                    if aria_label:
                                        match = re.search(r'(\d+(\.\d+)?)', aria_label)
                                        if match: rating = match.group(1)

                                # =============================
                                # 提取日期 (已修复错位问题)
                                # =============================
                                date = ""
                                # 1. 优先尝试使用最准确的唯一类名
                                date_elem = r.locator('.rsqaWe').first
                                if date_elem.is_visible():
                                    date = date_elem.inner_text().strip()
                                
                                # 2. 如果类名失效，启动特征词备用方案
                                if not date:
                                    try:
                                        # 寻找包含 "前" (日语) 或 "ago" (英语) 的 span 元素
                                        time_elements = r.locator('span:has-text("前"), span:has-text("ago")').all()
                                        for elem in time_elements:
                                            text = elem.inner_text().strip()
                                            # 日期文本通常很短（如 "1 か月前"），
                                            # 我们限制长度 < 15，并排除包含"ローカル"的干扰项，避免误抓正文
                                            if 0 < len(text) < 15 and "ローカル" not in text:
                                                date = text
                                                break
                                    except:
                                        pass

                                # 提取用户名
                                reviewer = ""
                                reviewer_elem = r.locator('.d4r55').first
                                if reviewer_elem.is_visible():
                                    reviewer = reviewer_elem.inner_text().strip()

                                unique_reviews[rid] = {
                                    "place": place_name,
                                    "reviewer": reviewer,
                                    "text": text,
                                    "rating": rating,
                                    "date": date
                                }
                                new_found_in_this_scroll += 1
                        except Exception as e:
                            continue # 忽略单条提取错误，继续下一条

                # 3. 报告进度
                if i % 2 == 0:
                    print(f"   已滚动 {i+1}/{max_scrolls} 次 | 当前总共收集到不重复评论: {len(unique_reviews)} 条")

                # 4. 执行向下滚动
                scroll_container.evaluate("el => el.scrollTop = el.scrollHeight")
                page.wait_for_timeout(2500) # 等待 Google Maps 异步加载新评论

                # 5. 停止条件判断 (如果连续3次滚动都没有提取到新评论，说明可能到底了)
                if new_found_in_this_scroll == 0:
                    consecutive_no_new_reviews += 1
                else:
                    consecutive_no_new_reviews = 0

                if consecutive_no_new_reviews >= 3:
                    print("   连续3次滚动未发现新评论，已到达底部或加载完毕。")
                    break

        except Exception as e:
            print(f"❌ 抓取过程中断: {place_name}\n错误详情: {e}")
        finally:
            browser.close()

    # 将字典转换为列表返回
    return list(unique_reviews.values())

# =============================
# 主程序
# =============================
if __name__ == "__main__":

    csv_file = r"E:\工作文件\2026论文2\论文代码\places.csv"
    output_file = r"E:\工作文件\2026论文2\论文代码\reviews_output4.csv"

    if not os.path.exists(csv_file):
        print(f"⚠️ 未找到 CSV 文件 {csv_file}，将使用测试 URL 进行演示。")
        places = [
            ("Musashi-Koyama Shopping Street", "https://www.google.co.jp/maps/place/Musashi-Koyama+Shopping+Street+%E2%80%9CPalm%E2%80%9D/@35.6185659,139.702987,17z/data=!4m8!3m7!1s0x60188adb9bc5775d:0x54c854a315ba0ee8!8m2!3d35.6185659!4d139.7055673!9m1!1b1!16s%2Fg%2F1tj4bgdd?entry=ttu&g_ep=EgoyMDI2MDQxMy4wIKXMDSoASAFQAw%3D%3D")
        ]
        output_file = "reviews_output4.csv"
    else:
        places = read_places(csv_file)

    all_data = []

    for name, url in places:
        # 将 max_scrolls 调高以适应拱廊街可能存在的大量评论
        data = scrape_reviews(name, url, max_scrolls=150)  
        all_data.extend(data)
        time.sleep(random.uniform(3, 6))

    if all_data:
        df = pd.DataFrame(all_data)
        df.to_csv(output_file, index=False, encoding="utf-8-sig")
        print(f"\n🎉 全部完成！数据已保存至: {output_file}")
        print(f"总计抓取 {len(all_data)} 条独立评论。")
    else:
        print("\n❌ 未抓取到任何数据。")
