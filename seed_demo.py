"""Демо-данные: небольшое семейное дерево для проверки API."""

from datetime import date

from app.database import SessionLocal, init_db
from app.models import Gender, RelationType
from app.schemas import PersonCreate, RelationshipCreate
from app.services.tree_service import TreeService


def main() -> None:
    init_db()
    db = SessionLocal()
    service = TreeService(db)

    # Если уже есть люди — не дублируем
    if service.list_persons(limit=1):
        print("База уже содержит данные, seed пропущен.")
        db.close()
        return

    people = {
        "ivan": service.create_person(
            PersonCreate(
                first_name="Иван",
                last_name="Иванов",
                gender=Gender.MALE,
                birth_date=date(1950, 3, 12),
            )
        ),
        "maria": service.create_person(
            PersonCreate(
                first_name="Мария",
                last_name="Иванова",
                maiden_name="Петрова",
                gender=Gender.FEMALE,
                birth_date=date(1952, 7, 5),
            )
        ),
        "sergey": service.create_person(
            PersonCreate(
                first_name="Сергей",
                last_name="Иванов",
                gender=Gender.MALE,
                birth_date=date(1975, 1, 20),
            )
        ),
        "anna": service.create_person(
            PersonCreate(
                first_name="Анна",
                last_name="Иванова",
                maiden_name="Сидорова",
                gender=Gender.FEMALE,
                birth_date=date(1978, 11, 2),
            )
        ),
        "olga": service.create_person(
            PersonCreate(
                first_name="Ольга",
                last_name="Иванова",
                gender=Gender.FEMALE,
                birth_date=date(1980, 4, 15),
            )
        ),
        "dmitry": service.create_person(
            PersonCreate(
                first_name="Дмитрий",
                last_name="Иванов",
                gender=Gender.MALE,
                birth_date=date(2005, 9, 8),
            )
        ),
        "elena": service.create_person(
            PersonCreate(
                first_name="Елена",
                last_name="Иванова",
                gender=Gender.FEMALE,
                birth_date=date(2008, 2, 28),
            )
        ),
    }

    links = [
        # Иван + Мария — супруги, родители Сергея и Ольги
        (people["ivan"].id, people["maria"].id, RelationType.SPOUSE_OF),
        (people["ivan"].id, people["sergey"].id, RelationType.PARENT_OF),
        (people["maria"].id, people["sergey"].id, RelationType.PARENT_OF),
        (people["ivan"].id, people["olga"].id, RelationType.PARENT_OF),
        (people["maria"].id, people["olga"].id, RelationType.PARENT_OF),
        # Сергей + Анна — супруги, родители Дмитрия и Елены
        (people["sergey"].id, people["anna"].id, RelationType.SPOUSE_OF),
        (people["sergey"].id, people["dmitry"].id, RelationType.PARENT_OF),
        (people["anna"].id, people["dmitry"].id, RelationType.PARENT_OF),
        (people["sergey"].id, people["elena"].id, RelationType.PARENT_OF),
        (people["anna"].id, people["elena"].id, RelationType.PARENT_OF),
    ]

    for a, b, t in links:
        service.add_relationship(
            RelationshipCreate(person_id=a, related_person_id=b, relation_type=t)
        )

    print("Демо-дерево создано:")
    for key, p in people.items():
        print(f"  {key:8} id={p.id}  {p.full_name}")
    print("\nПример: GET /api/v1/tree/{id}  (id Сергея =", people["sergey"].id, ")")
    db.close()


if __name__ == "__main__":
    main()
