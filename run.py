"""Development entry point."""

import warnings

# botocore internally calls datetime.utcnow() which is deprecated in Python 3.12+.
# This is a third-party issue; suppress the warning so logs stay clean.
warnings.filterwarnings(
    "ignore",
    message="datetime.datetime.utcnow",
    category=DeprecationWarning,
)

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000, threaded=True)
