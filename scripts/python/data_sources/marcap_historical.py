"""Historical KRX market-cap reader backed by the FinanceData/marcap GitHub
repo (https://github.com/FinanceData/marcap).

The upstream ships yearly gzipped CSV files at
    https://raw.githubusercontent.com/FinanceData/marcap/refs/heads/master/data/marcap-YYYY.csv.gz

There is no PyPI package. We download only the yearly file(s) needed and
cache them under scripts/python/.cache/marcap/ (gitignored), then parse
with pandas.

Resolution strategy, in priority order:
    1. Try the canonical URL pattern (master branch, refs/heads).
    2. Try alternative branches (main) and legacy path layouts (kept for
       resilience against repo restructuring).
    3. If every raw URL fails (e.g. egress blocking, GitHub rate-limit),
       fall back to a `git clone --depth 1` of the entire repo into
       scripts/python/.cache/marcap/repo and read the file from there.

Public API:
    fetch_marcap_date(date)          -> DataFrame for a single trading date
                                        (auto-falls back to nearest prior
                                        trading day if exact match missing).
    fetch_marcap_range(start, end,
                       tickers=None) -> DataFrame for a date range.

Both functions return a DataFrame with columns normalized to:
    date, ticker, name, open, high, low, close, volume, trading_value,
    market_cap, shares_outstanding, market, dept, rank
"""

import os
import sys
import shutil
import subprocess
import urllib.request
import urllib.error
from datetime import datetime, timedelta

import pandas as pd

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CACHE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", ".cache", "marcap",
)
CACHE_DIR = os.path.normpath(CACHE_DIR)

REPO = "FinanceData/marcap"
REPO_GIT_URL = "https://github.com/FinanceData/marcap.git"

# Candidate URL patterns, tried in this exact order. As of late 2024 the repo
# stores yearly data as Parquet files (e.g. marcap-2024.parquet) at /data/.
# CSV.gz patterns are kept as a defensive fallback for older snapshots /
# mirrors of the dataset.
#
# Each tuple is (url_template, filename_for_cache).
URL_TEMPLATES = (
    # ---- Parquet (current canonical format) ----
    ("https://raw.githubusercontent.com/{repo}/refs/heads/master/data/marcap-{year}.parquet",
     "marcap-{year}.parquet"),
    ("https://raw.githubusercontent.com/{repo}/refs/heads/main/data/marcap-{year}.parquet",
     "marcap-{year}.parquet"),
    ("https://raw.githubusercontent.com/{repo}/master/data/marcap-{year}.parquet",
     "marcap-{year}.parquet"),
    ("https://raw.githubusercontent.com/{repo}/main/data/marcap-{year}.parquet",
     "marcap-{year}.parquet"),
    # ---- CSV.gz (legacy / mirror fallback) ----
    ("https://raw.githubusercontent.com/{repo}/refs/heads/master/data/marcap-{year}.csv.gz",
     "marcap-{year}.csv.gz"),
    ("https://raw.githubusercontent.com/{repo}/refs/heads/main/data/marcap-{year}.csv.gz",
     "marcap-{year}.csv.gz"),
    ("https://raw.githubusercontent.com/{repo}/master/data/marcap-{year}.csv.gz",
     "marcap-{year}.csv.gz"),
    ("https://raw.githubusercontent.com/{repo}/main/data/marcap-{year}.csv.gz",
     "marcap-{year}.csv.gz"),
    # ---- Nested / digit-only fallbacks ----
    ("https://raw.githubusercontent.com/{repo}/refs/heads/master/marcap/data/marcap-{year}.parquet",
     "marcap-{year}.parquet"),
    ("https://raw.githubusercontent.com/{repo}/refs/heads/master/marcap/data/marcap-{year}.csv.gz",
     "marcap-{year}.csv.gz"),
)


def _yearly_url_candidates(year):
    """Yield (url, dest_filename) pairs for a given year, in priority order."""
    for tmpl, dest_tmpl in URL_TEMPLATES:
        yield (
            tmpl.format(repo=REPO, year=year),
            dest_tmpl.format(year=year),
        )


def _repo_clone_dir():
    """Path where the repo gets shallow-cloned if raw URLs fail."""
    return os.path.join(CACHE_DIR, "repo")


def _file_in_clone(year):
    """Path to the yearly file inside the cloned repo. Tries parquet first
    (current canonical), then csv.gz, in both /data/ and /marcap/data/
    layouts. Returns the matched path or None.
    """
    base = _repo_clone_dir()
    candidates = [
        # Parquet first
        os.path.join(base, "data", "marcap-{0}.parquet".format(year)),
        os.path.join(base, "marcap", "data", "marcap-{0}.parquet".format(year)),
        # CSV.gz fallbacks
        os.path.join(base, "data", "marcap-{0}.csv.gz".format(year)),
        os.path.join(base, "marcap", "data", "marcap-{0}.csv.gz".format(year)),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def _cached_file_for_year(year):
    """Return the cached yearly file path if any extension is present.

    Checks parquet first, then csv.gz. Returns None if nothing cached.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    for ext in ("parquet", "csv.gz"):
        p = os.path.join(CACHE_DIR, "marcap-{0}.{1}".format(year, ext))
        if os.path.exists(p):
            return p
    return None


def _cache_path_for_filename(filename):
    """Compose absolute path under the cache dir for a given filename."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, filename)


def cache_dir():
    """Return the absolute cache directory path (for diagnostics)."""
    return CACHE_DIR


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def _download(url, dest_path, verbose=True):
    """Stream `url` to `dest_path`. Atomic via temp file rename."""
    tmp = dest_path + ".part"
    try:
        if verbose:
            print("  Downloading {0} ...".format(url),
                  end=" ", flush=True)
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "korean-stock-ranker/1.0"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            total_bytes = 0
            with open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    total_bytes += len(chunk)
        os.rename(tmp, dest_path)
        if verbose:
            print("OK ({0:,} bytes)".format(total_bytes), flush=True)
        return True
    except urllib.error.HTTPError as e:
        if verbose:
            print("HTTP {0}".format(e.code), flush=True)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    except Exception as e:
        if verbose:
            print("FAILED ({0})".format(e), flush=True)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def _shallow_clone_repo(verbose=True):
    """Shallow-clone the FinanceData/marcap repo into the cache.

    Used as the last-resort fallback when raw URL downloads fail. Idempotent:
    if the clone already exists, returns its path immediately.
    """
    clone_dir = _repo_clone_dir()
    if os.path.isdir(os.path.join(clone_dir, ".git")):
        if verbose:
            print("  Existing clone detected at {0}, reusing.".format(clone_dir),
                  flush=True)
        return clone_dir

    # Make sure parent exists
    os.makedirs(CACHE_DIR, exist_ok=True)

    # Remove a partial/non-git directory if it exists
    if os.path.exists(clone_dir):
        shutil.rmtree(clone_dir, ignore_errors=True)

    if shutil.which("git") is None:
        if verbose:
            print("  ERROR: git not found in PATH. Install git or fix your "
                  "PATH to use the clone fallback.", flush=True)
        return None

    if verbose:
        print("  Falling back to: git clone --depth 1 {0} {1}".format(
            REPO_GIT_URL, clone_dir), flush=True)
    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", REPO_GIT_URL, clone_dir],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            if verbose:
                print("  Clone failed (exit {0}):".format(result.returncode),
                      flush=True)
                err = (result.stderr or "").strip()
                if err:
                    print("    {0}".format(err[:500]), flush=True)
            return None
    except subprocess.TimeoutExpired:
        if verbose:
            print("  Clone timed out after 10 minutes.", flush=True)
        return None
    except Exception as e:
        if verbose:
            print("  Clone error: {0}".format(e), flush=True)
        return None

    if verbose:
        print("  Clone complete.", flush=True)
    return clone_dir


def _ensure_yearly_file(year, force=False, verbose=True):
    """Ensure the yearly file is in the cache. Returns local path or None.

    Strategy:
      1. If already cached (either .parquet or .csv.gz), return it.
      2. Try each URL pattern (parquet first, csv.gz fallback). Each
         download writes to its own filename so we preserve the format.
      3. If all URLs fail, shallow-clone the entire repo and copy the file
         from the clone into the cache (parquet preferred).
    """
    cached = _cached_file_for_year(year)
    if cached and not force:
        if verbose:
            try:
                size = os.path.getsize(cached)
            except OSError:
                size = 0
            print("  Using cached file: {0} ({1:,} bytes)".format(cached, size),
                  flush=True)
        return cached

    tried = []
    for url, dest_filename in _yearly_url_candidates(year):
        tried.append(url)
        dest_path = _cache_path_for_filename(dest_filename)
        if _download(url, dest_path, verbose=verbose):
            return dest_path

    if verbose:
        print("", flush=True)
        print("  All raw URL candidates failed for marcap-{0}:".format(year),
              flush=True)
        for u in tried:
            print("    - {0}".format(u), flush=True)
        print("", flush=True)

    # Last-resort fallback: shallow-clone the repo.
    clone_dir = _shallow_clone_repo(verbose=verbose)
    if clone_dir is None:
        return None

    src = _file_in_clone(year)
    if src is None:
        if verbose:
            print("  Clone succeeded but marcap-{0}.(parquet|csv.gz) not "
                  "found inside it. Listing data dirs for diagnosis:".format(year),
                  flush=True)
            for sub in ("data", "marcap/data"):
                d = os.path.join(clone_dir, sub)
                if os.path.isdir(d):
                    files = sorted(os.listdir(d))[:8]
                    print("    {0}/: {1}".format(sub, ", ".join(files) or "(empty)"),
                          flush=True)
                else:
                    print("    {0}/: (does not exist)".format(sub), flush=True)
        return None

    # Copy the file out of the clone into the flat cache, preserving its
    # extension. Subsequent runs won't depend on the clone layout.
    src_ext = ".parquet" if src.endswith(".parquet") else ".csv.gz"
    dest = _cache_path_for_filename("marcap-{0}{1}".format(year, src_ext))
    try:
        shutil.copyfile(src, dest)
        if verbose:
            print("  Copied {0} -> {1}".format(src, dest), flush=True)
        return dest
    except OSError as e:
        if verbose:
            print("  Copy failed: {0}".format(e), flush=True)
        return src  # use it in place if copy failed


# ---------------------------------------------------------------------------
# Load / normalize
# ---------------------------------------------------------------------------

_COLUMN_RENAME = {
    "Code": "ticker",
    "Name": "name",
    "Date": "date",
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Volume": "volume",
    "Amount": "trading_value",
    "Marcap": "market_cap",
    "Stocks": "shares_outstanding",
    "Market": "market",
    "Dept": "dept",
    "Rank": "rank",
}


def _normalize_df(df):
    """Apply column renames and type coercion.

    Returns a DataFrame with the standardized column set. Missing columns
    are tolerated (filled with NaN) so the caller can rely on the schema.
    """
    if df is None or len(df) == 0:
        return df

    rename = {col: _COLUMN_RENAME[col]
              for col in df.columns if col in _COLUMN_RENAME}
    df = df.rename(columns=rename)

    # Ensure ticker is string and zero-padded to 6 chars
    if "ticker" in df.columns:
        df["ticker"] = df["ticker"].astype(str).str.zfill(6)

    # Coerce date column to ISO string
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")

    # Ensure expected columns exist (fill missing with NaN)
    expected = ["date", "ticker", "name", "open", "high", "low", "close",
                "volume", "trading_value", "market_cap",
                "shares_outstanding", "market", "dept", "rank"]
    for col in expected:
        if col not in df.columns:
            df[col] = pd.NA

    return df[expected]


def _load_yearly(year, verbose=True):
    """Load the yearly file into a normalized DataFrame. Caches in memory.

    Dispatches by file extension: .parquet uses pd.read_parquet (requires
    pyarrow or fastparquet); .csv.gz uses pd.read_csv with gzip.
    """
    path = _ensure_yearly_file(year, verbose=verbose)
    if path is None:
        return None

    try:
        if path.endswith(".parquet"):
            try:
                df = pd.read_parquet(path)
            except ImportError as e:
                if verbose:
                    print("  ERROR reading parquet: {0}".format(e), flush=True)
                    print("        pyarrow is required. Run: pip install pyarrow",
                          flush=True)
                return None
        elif path.endswith(".csv.gz"):
            df = pd.read_csv(path, compression="gzip")
        else:
            # Try parquet, then csv.gz, as a last resort
            try:
                df = pd.read_parquet(path)
            except Exception:
                df = pd.read_csv(path, compression="infer")
    except Exception as e:
        if verbose:
            print("  ERROR reading {0}: {1}".format(path, e), flush=True)
        return None

    # Some parquet files have Date as the index rather than a column
    if df.index.name and "date" in str(df.index.name).lower():
        df = df.reset_index()

    return _normalize_df(df)


# Process-level cache to avoid re-loading the same year file multiple times
# during a single run.
_yearly_cache = {}


def _get_yearly_cached(year, verbose=True):
    if year in _yearly_cache:
        return _yearly_cache[year]
    df = _load_yearly(year, verbose=verbose)
    if df is not None:
        _yearly_cache[year] = df
    return df


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_marcap_date(date_iso, lookback_days=10, verbose=True):
    """Return marcap rows for a single trading date.

    If the exact date has no rows (weekend, holiday, market closed), falls
    back to the latest trading day within `lookback_days` previous calendar
    days.

    Args:
        date_iso: ISO date string YYYY-MM-DD.
        lookback_days: how far back to look if exact date missing.

    Returns:
        (df, actual_date) where df is the DataFrame for that single trading
        date, actual_date is its ISO string. Returns (None, None) if no
        data found in the window.
    """
    try:
        target = datetime.strptime(date_iso, "%Y-%m-%d").date()
    except ValueError:
        return None, None

    # Load the year(s) that cover the lookback window. Usually just one,
    # but handle year boundaries.
    years_needed = set()
    for delta in range(lookback_days + 1):
        d = target - timedelta(days=delta)
        years_needed.add(d.year)

    frames = []
    for year in sorted(years_needed):
        df_y = _get_yearly_cached(year, verbose=verbose)
        if df_y is not None:
            frames.append(df_y)
    if not frames:
        return None, None

    df_all = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]

    # Filter to the lookback window
    earliest = (target - timedelta(days=lookback_days)).isoformat()
    target_iso = target.isoformat()
    mask = (df_all["date"] >= earliest) & (df_all["date"] <= target_iso)
    df_window = df_all[mask]
    if len(df_window) == 0:
        return None, None

    actual_date = df_window["date"].max()
    df_filtered = df_window[df_window["date"] == actual_date].copy()
    return df_filtered, actual_date


def fetch_marcap_range(start_date, end_date, tickers=None, verbose=True):
    """Return marcap rows for a date range.

    Args:
        start_date, end_date: ISO date strings YYYY-MM-DD.
        tickers: optional list of tickers to filter to.

    Returns:
        DataFrame, or None if no data could be fetched.
    """
    try:
        s = datetime.strptime(start_date, "%Y-%m-%d").date()
        e = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        return None
    if e < s:
        return None

    years = list(range(s.year, e.year + 1))
    frames = []
    for year in years:
        df_y = _get_yearly_cached(year, verbose=verbose)
        if df_y is not None:
            frames.append(df_y)
    if not frames:
        return None

    df_all = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    mask = (df_all["date"] >= start_date) & (df_all["date"] <= end_date)
    df_window = df_all[mask].copy()
    if tickers:
        df_window = df_window[df_window["ticker"].isin(set(tickers))]
    return df_window


# ---------------------------------------------------------------------------
# Self-test (run as `python -m data_sources.marcap_historical 2024-12-30`)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "2024-12-30"
    print("Cache dir: {0}".format(cache_dir()))
    df, actual = fetch_marcap_date(target)
    if df is None:
        print("No data for {0}".format(target))
        sys.exit(1)
    print("Trading date: {0}".format(actual))
    print("Rows:         {0}".format(len(df)))
    print("Columns:      {0}".format(list(df.columns)))
    print("Samsung 005930 sample:")
    samsung = df[df["ticker"] == "005930"]
    if len(samsung) > 0:
        print(samsung.iloc[0].to_dict())
    else:
        print("  not found")
