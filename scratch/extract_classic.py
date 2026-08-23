import subprocess
import codecs
import sys

def extract(git_path, local_path):
    res = subprocess.run(["git", "show", f"HEAD:{git_path}"], capture_output=True)
    if res.returncode == 0:
        content = res.stdout.decode("utf-8")
        with codecs.open(local_path, "w", "utf-8") as f:
            f.write(content)
        print(f"Successfully extracted {git_path} to {local_path}")
    else:
        print(f"Failed to extract {git_path}: {res.stderr.decode('utf-8')}", file=sys.stderr)

extract("laliga_dashboard/styles.css", "laliga_dashboard/styles_classic.css")
extract("laliga_dashboard/index.html", "laliga_dashboard/classic.html")
extract("laliga_dashboard/match.html", "laliga_dashboard/match_classic.html")
