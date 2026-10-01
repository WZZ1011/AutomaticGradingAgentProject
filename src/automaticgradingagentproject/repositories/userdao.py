from automaticgradingagentproject.models.user import Student, Teacher
from automaticgradingagentproject.repositories.basedao import BaseRepository


class StudentDao(BaseRepository[Student]):
    model = Student

class TeacherDao(BaseRepository[Teacher]):
    model = Teacher