from automaticgradingagentproject.models.user import User
from automaticgradingagentproject.repositories.basedao import BaseRepository


class UserDao(BaseRepository[User]):
    model = User

