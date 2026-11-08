import numpy as np, pandas as pd
from pathlib import Path
rng = np.random.default_rng(0)
raw = Path(__file__).resolve().parent.parent / "data" / "raw"
raw.mkdir(parents=True, exist_ok=True)
n = 6000
states = ["SP","RJ","MG","RS","PR","SC","BA","DF","GO","ES","PE","CE"]
cats_pt = [f"cat_{i}" for i in range(14)]
cust = pd.DataFrame({"customer_id":[f"c{i}" for i in range(n)],
                     "customer_unique_id":[f"u{i}" for i in range(n)],
                     "customer_state": rng.choice(states, n, p=[.4,.13,.12]+[.35/9]*9)})
ts = pd.to_datetime("2016-10-01") + pd.to_timedelta(rng.integers(0, 700, n), unit="D") + pd.to_timedelta(rng.integers(0,86400,n), unit="s")
est = ts + pd.to_timedelta(rng.integers(10, 30, n), unit="D")
delay = rng.choice([-5,-2,0,2,5,10,20], n, p=[.4,.25,.2,.04,.04,.04,.03])
deliv = est + pd.to_timedelta(delay, unit="D")
status = rng.choice(["delivered","canceled","shipped"], n, p=[.93,.04,.03])
deliv = pd.Series(deliv).astype(object)
deliv[status!="delivered"] = np.nan
orders = pd.DataFrame({"order_id":[f"o{i}" for i in range(n)], "customer_id":cust.customer_id,
    "order_status":status, "order_purchase_timestamp":ts.astype(str),
    "order_delivered_customer_date":[("" if pd.isna(d) else str(d)) for d in deliv],
    "order_estimated_delivery_date":est.astype(str)})
prods = pd.DataFrame({"product_id":[f"p{i}" for i in range(60)], "product_category_name":rng.choice(cats_pt+[None], 60)})
m = n*2
items = pd.DataFrame({"order_id":rng.choice(orders.order_id, m), "product_id":rng.choice(prods.product_id, m),
    "price":rng.gamma(3,40,m).round(2), "freight_value":rng.gamma(2,8,m).round(2)})
rev = pd.DataFrame({"review_id":[f"r{i}" for i in range(n)], "order_id":orders.order_id,
    "review_score": np.where(delay>3, rng.integers(1,3,n), rng.integers(3,6,n))})
rev = pd.concat([rev, rev.iloc[:50].assign(review_id=lambda d: "x"+d.review_id, review_score=5)])
trans = pd.DataFrame({"product_category_name":cats_pt[:10], "product_category_name_english":[f"english_{c}" for c in cats_pt[:10]]})
cust.to_csv(raw/"olist_customers_dataset.csv", index=False)
orders.to_csv(raw/"olist_orders_dataset.csv", index=False)
items.to_csv(raw/"olist_order_items_dataset.csv", index=False)
rev.to_csv(raw/"olist_order_reviews_dataset.csv", index=False)
prods.to_csv(raw/"olist_products_dataset.csv", index=False)
trans.to_csv(raw/"product_category_name_translation.csv", index=False)
print("fake data written")
