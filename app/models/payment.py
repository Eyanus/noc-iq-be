from datetime import datetime
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# Maximum time a transaction can remain unsubmitted (seconds)
TRANSACTION_TIMEOUT_MAX_SECONDS = 300  # 5 minutes
# Default expiry window for unconfirmed transactions
TRANSACTION_EXPIRY_WINDOW_SECONDS = TRANSACTION_TIMEOUT_MAX_SECONDS
