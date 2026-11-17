"""Error types.

The worker sorts every failure into one of two groups:

* ``PermanentError``: a retry gives the same result (bad message, host not
  allowed, HTTP 404, file is not an image). The message goes to the DLQ at once.
* any other exception: maybe temporary (timeout, HTTP 503, S3 throttling). The
  message goes back to the queue and SQS retries it until ``maxReceiveCount``.
"""


class PermanentError(Exception):
    """A failure that a retry cannot fix."""


class InvalidMessage(PermanentError):
    """The SQS message body is not a valid request."""


class FetchRejected(PermanentError):
    """The URL, the redirect target or the response is not allowed."""


class UnsupportedImage(PermanentError):
    """The downloaded bytes are not an image we accept."""


class TransientFetchError(Exception):
    """A download failure that can succeed on a later attempt."""
