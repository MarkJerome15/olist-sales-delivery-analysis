"""
Olist sales analysis pipeline.

One command rebuilds everything:
    CSVs  ->  SQLite database  ->  SQL queries  ->  CSV results  ->  charts

Usage (from the project folder):
    py run_pipeline.py              # build the database if missing, then run everything
    py run_pipeline.py --rebuild    # force a fresh database from the raw CSVs

Expected layout:
    data/raw/   the 9 Olist CSV files (olist_orders_dataset.csv, ...)
    outputs/    query results (created automatically)
    images/     charts (created automatically)
"""
import sqlite3
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # draw to files, no window needed
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "outputs"
IMG = ROOT / "images"
DB = ROOT / "olist.db"

# --------------------------------------------------------------------------
# SQL queries. Revenue = item price on delivered orders, from 2017-01 onward.
# --------------------------------------------------------------------------
QUERIES = {
    "q1_monthly_revenue": """
        WITH monthly AS (
            SELECT
                strftime('%Y-%m', o.order_purchase_timestamp) AS month,
                COUNT(DISTINCT o.order_id)                    AS orders,
                ROUND(SUM(oi.price), 2)                       AS revenue
            FROM olist_orders_dataset o
            JOIN olist_order_items_dataset oi ON o.order_id = oi.order_id
            WHERE o.order_status = 'delivered'
              AND o.order_purchase_timestamp >= '2017-01-01'
            GROUP BY month
        )
        SELECT
            month,
            orders,
            revenue,
            ROUND((revenue - LAG(revenue) OVER (ORDER BY month))
                  * 100.0 / LAG(revenue) OVER (ORDER BY month), 1) AS mom_growth_pct,
            ROUND(SUM(revenue) OVER (ORDER BY month), 2)           AS running_total
        FROM monthly
        ORDER BY month;
    """,
    "q2_categories": """
        SELECT
            COALESCE(t.product_category_name_english, p.product_category_name) AS category,
            COUNT(DISTINCT oi.order_id)  AS orders,
            ROUND(SUM(oi.price), 2)      AS revenue,
            ROUND(SUM(oi.price) / COUNT(DISTINCT oi.order_id), 2) AS avg_revenue_per_order,
            ROUND(SUM(oi.price) * 100.0 /
                (SELECT SUM(oi2.price)
                 FROM olist_order_items_dataset oi2
                 JOIN olist_orders_dataset o2 ON oi2.order_id = o2.order_id
                 WHERE o2.order_status = 'delivered'), 1) AS pct_of_revenue
        FROM olist_order_items_dataset oi
        JOIN olist_orders_dataset o ON oi.order_id = o.order_id
        JOIN olist_products_dataset p ON oi.product_id = p.product_id
        LEFT JOIN product_category_name_translation t
               ON p.product_category_name = t.product_category_name
        WHERE o.order_status = 'delivered'
        GROUP BY category
        ORDER BY revenue DESC
        LIMIT 10;
    """,
    "q2_states": """
        SELECT
            c.customer_state                                    AS state,
            COUNT(DISTINCT o.order_id)                          AS orders,
            ROUND(SUM(oi.price), 2)                             AS revenue,
            ROUND(SUM(oi.price) / COUNT(DISTINCT o.order_id), 2) AS avg_revenue_per_order
        FROM olist_orders_dataset o
        JOIN olist_order_items_dataset oi ON o.order_id = oi.order_id
        JOIN olist_customers_dataset c ON o.customer_id = c.customer_id
        WHERE o.order_status = 'delivered'
        GROUP BY c.customer_state
        ORDER BY revenue DESC
        LIMIT 10;
    """,
    "q4_delay_vs_reviews": """
        WITH rev AS (
            SELECT order_id, AVG(review_score) AS score
            FROM olist_order_reviews_dataset
            GROUP BY order_id
        ),
        d AS (
            SELECT
                o.order_id,
                julianday(o.order_delivered_customer_date)
                  - julianday(o.order_estimated_delivery_date) AS days_late
            FROM olist_orders_dataset o
            WHERE o.order_status = 'delivered'
              AND o.order_delivered_customer_date IS NOT NULL
              AND o.order_delivered_customer_date <> ''
        )
        SELECT
            CASE
                WHEN days_late <= 0  THEN '1. On time or early'
                WHEN days_late <= 3  THEN '2. 1-3 days late'
                WHEN days_late <= 7  THEN '3. 4-7 days late'
                WHEN days_late <= 14 THEN '4. 8-14 days late'
                ELSE                      '5. 15+ days late'
            END                    AS delay_bucket,
            COUNT(*)               AS orders,
            ROUND(AVG(r.score), 2) AS avg_review_score
        FROM d
        JOIN rev r ON d.order_id = r.order_id
        GROUP BY delay_bucket
        ORDER BY delay_bucket;
    """,
    # Order-level table used for the Excel pivot and the Power BI dashboard.
    "order_items_flat": """
        SELECT
            o.order_id,
            strftime('%Y', o.order_purchase_timestamp) AS year,
            'Q' || ((CAST(strftime('%m', o.order_purchase_timestamp) AS INTEGER) + 2) / 3) AS quarter,
            strftime('%Y-%m', o.order_purchase_timestamp) AS month,
            COALESCE(t.product_category_name_english, p.product_category_name) AS category,
            c.customer_state AS state,
            oi.price,
            oi.freight_value
        FROM olist_orders_dataset o
        JOIN olist_order_items_dataset oi ON o.order_id = oi.order_id
        JOIN olist_products_dataset p     ON oi.product_id = p.product_id
        LEFT JOIN product_category_name_translation t
               ON p.product_category_name = t.product_category_name
        JOIN olist_customers_dataset c    ON o.customer_id = c.customer_id
        WHERE o.order_status = 'delivered'
          AND o.order_purchase_timestamp >= '2017-01-01';
    """,
}


def build_database(rebuild: bool) -> sqlite3.Connection:
    """Load every CSV in data/raw into SQLite (table name = file name)."""
    if rebuild and DB.exists():
        DB.unlink()
    if DB.exists():
        print(f"Using existing database: {DB.name} (use --rebuild to recreate)")
        return sqlite3.connect(DB)

    csv_files = sorted(RAW.glob("*.csv"))
    if not csv_files:
        sys.exit(f"No CSV files found in {RAW}. Put the Olist CSVs there and run again.")

    con = sqlite3.connect(DB)
    for csv in csv_files:
        pd.read_csv(csv).to_sql(csv.stem, con, if_exists="replace", index=False)
        print(f"  loaded {csv.stem}")
    return con


def run_queries(con: sqlite3.Connection) -> dict:
    """Run each query and save its result to outputs/<name>.csv."""
    OUT.mkdir(exist_ok=True)
    results = {}
    for name, sql in QUERIES.items():
        df = pd.read_sql_query(sql, con)
        df.to_csv(OUT / f"{name}.csv", index=False)
        results[name] = df
        print(f"  {name}: {len(df):,} rows")
    return results


# --------------------------------------------------------------------------
# Charts
# --------------------------------------------------------------------------
def chart_monthly_revenue(q1: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(q1["month"], q1["revenue"] / 1000, marker="o", linewidth=2, color="#1f77b4")

    # Mark the peak revenue month (found from the data, not hard-coded).
    pos = int(q1["revenue"].reset_index(drop=True).idxmax())
    peak = q1.iloc[pos]
    on_right_half = pos > len(q1) / 3   # put the label on whichever side has room
    ax.annotate(
        f"Peak: {peak['month']} (R${peak['revenue'] / 1000:,.0f}K)",
        xy=(peak["month"], peak["revenue"] / 1000),
        xytext=(-18 if on_right_half else 18, -4),
        textcoords="offset points",
        ha="right" if on_right_half else "left",
        va="center",
        arrowprops=dict(arrowstyle="->"),
    )
    ax.set_title("Monthly revenue, delivered orders")
    ax.set_ylabel("Revenue (R$ thousands)")
    ax.set_xlabel("Month")
    ax.grid(alpha=0.3)
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(IMG / "q1_monthly_revenue.png", dpi=150)
    plt.close(fig)


def chart_categories(q2: pd.DataFrame) -> None:
    q2 = q2.copy()
    q2["category"] = q2["category"].fillna("(no category)")  # some products have none
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    cat = q2.sort_values("revenue")
    axes[0].barh(cat["category"], cat["revenue"] / 1000, color="#1f77b4")
    axes[0].set_title("Top 10 categories by revenue")
    axes[0].set_xlabel("Revenue (R$ thousands)")

    axes[1].scatter(q2["orders"], q2["avg_revenue_per_order"], s=80, color="#d62728")
    for _, r in q2.iterrows():
        axes[1].annotate(r["category"], (r["orders"], r["avg_revenue_per_order"]),
                         xytext=(5, 5), textcoords="offset points", fontsize=8)
    axes[1].set_title("Volume vs. average order value")
    axes[1].set_xlabel("Number of orders")
    axes[1].set_ylabel("Avg revenue per order (R$)")
    axes[1].grid(alpha=0.3)

    plt.tight_layout()
    plt.savefig(IMG / "q2_categories.png", dpi=150)
    plt.close(fig)


def chart_states(q2s: pd.DataFrame) -> None:
    st = q2s.sort_values("revenue")
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.barh(st["state"], st["revenue"] / 1e6, color="#1f77b4")
    for i, r in enumerate(st["revenue"]):
        ax.text(r / 1e6 + 0.05, i, f"R${r / 1e6:.2f}M", va="center", fontsize=9)
    ax.set_title("Top 10 states by revenue")
    ax.set_xlabel("Revenue (R$ millions)")
    ax.set_xlim(0, st["revenue"].max() / 1e6 * 1.2)
    plt.tight_layout()
    plt.savefig(IMG / "q2_states.png", dpi=150)
    plt.close(fig)


def chart_delay_vs_reviews(q4: pd.DataFrame) -> None:
    labels = q4["delay_bucket"].str[3:]  # drop the "1. " sort prefix
    colors = ["#2ca02c", "#ffbb78", "#ff7f0e", "#d62728", "#d62728"][: len(q4)]

    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.bar(labels, q4["avg_review_score"], color=colors)
    for bar, n in zip(bars, q4["orders"]):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.05,
                f"{bar.get_height():.2f}\n({n:,} orders)", ha="center", fontsize=9)
    ax.set_ylim(0, 5)
    ax.set_title("Average review score by delivery delay")
    ax.set_ylabel("Avg review score (1-5)")
    ax.set_xlabel("Delivery vs. estimated date")
    plt.xticks(rotation=15)
    plt.tight_layout()
    plt.savefig(IMG / "q4_delay_vs_reviews.png", dpi=150)
    plt.close(fig)


def main() -> None:
    rebuild = "--rebuild" in sys.argv
    IMG.mkdir(exist_ok=True)

    print("1/3 Database")
    con = build_database(rebuild)

    print("2/3 Queries")
    results = run_queries(con)
    con.close()

    print("3/3 Charts")
    chart_monthly_revenue(results["q1_monthly_revenue"])
    chart_categories(results["q2_categories"])
    chart_states(results["q2_states"])
    chart_delay_vs_reviews(results["q4_delay_vs_reviews"])
    print(f"Done. Results in {OUT.name}/, charts in {IMG.name}/")


if __name__ == "__main__":
    main()
