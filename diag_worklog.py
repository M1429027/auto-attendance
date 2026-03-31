"""診斷腳本：列印點擊「填寫工作日誌」後的視窗和 iframe 結構"""
from main import load_config
from attendance import init_browser, navigate_to_attendance
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

config = load_config()
driver = init_browser(headless=False)
navigate_to_attendance(driver, config["url"], config["browser"]["wait_timeout"])

# 在 main iframe 裡找並點擊填寫工作日誌
driver.switch_to.default_content()
driver.switch_to.frame("main")

handles_before = set(driver.window_handles)
print("Handles before:", list(driver.window_handles))

try:
    btn = driver.find_element(By.CSS_SELECTOR, "[id*='btnOpenWorkLog']")
    print("Button id:", btn.get_attribute("id"))
    driver.execute_script("arguments[0].click();", btn)
    print("Clicked!")
except Exception as e:
    print("Button not found:", e)

time.sleep(4)
print("Handles after:", list(driver.window_handles))
print("New handles:", set(driver.window_handles) - handles_before)

# 列出所有 iframe（在 default_content）
driver.switch_to.default_content()
iframes = driver.find_elements(By.TAG_NAME, "iframe")
print(f"\nAll iframes in page ({len(iframes)}):")
for i, f in enumerate(iframes):
    src = (f.get_attribute("src") or "")[:80]
    print(f"  [{i}] id={f.get_attribute('id')} name={f.get_attribute('name')} src={src}")

# 試切到每個 iframe 找 WorkSummary
print("\nSearching for COLCTRLWorkSummary in each iframe...")
for i, f in enumerate(iframes):
    try:
        driver.switch_to.default_content()
        driver.switch_to.frame(f)
        els = driver.find_elements(By.CSS_SELECTOR, "[id$='COLCTRLWorkSummary']")
        if els:
            print(f"  FOUND in iframe[{i}]:", [el.get_attribute("id") for el in els])
            break
        # 再試 nested iframe
        nested = driver.find_elements(By.TAG_NAME, "iframe")
        for ni, nf in enumerate(nested):
            try:
                driver.switch_to.frame(nf)
                nels = driver.find_elements(By.CSS_SELECTOR, "[id$='COLCTRLWorkSummary']")
                if nels:
                    print(f"  FOUND in iframe[{i}].nested[{ni}]:", [el.get_attribute("id") for el in nels])
                driver.switch_to.parent_frame()
            except Exception as ne:
                print(f"  nested iframe[{ni}] err: {ne}")
    except Exception as e:
        print(f"  iframe[{i}] err: {e}")

print("\nDone. Sleeping 5s then quit.")
time.sleep(5)
driver.quit()
