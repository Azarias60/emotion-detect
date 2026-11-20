"""Identity 用例错误。"""


class IdentityError(Exception):
    """Identity 边界上的业务错误。"""


class UsernameTakenError(IdentityError):
    pass


class InvalidCredentialsError(IdentityError):
    pass


class LoginRateLimitedError(IdentityError):
    pass


class UserNotFoundError(IdentityError):
    pass


class InvalidPasswordError(IdentityError):
    pass
