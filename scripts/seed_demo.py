#!/usr/bin/env python3
"""Наполнить БД тестовыми демо-данными (вымышленная семья Тестовых).

Для dev-стенда отладки фич: прод-БД не трогает. Данные полностью вымышленные.

Запуск:
    FAMILY_TREE_DATABASE_URL=sqlite:///data/dev.db .venv/bin/python scripts/seed_demo.py
    --force — пересоздать, даже если люди уже есть (сносит ВСЁ в этой БД).
"""
import argparse
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, delete, func, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.config import settings  # noqa: E402
from app.models import Base, Gender, Person, RelationType, Relationship  # noqa: E402

log = logging.getLogger("seed_demo")


def person(first, last, gender, birth=None, death=None, middle=None,
           maiden=None, birth_place=None, notes=None) -> dict:
    return dict(first_name=first, last_name=last, middle_name=middle,
                maiden_name=maiden, gender=gender, birth_date=birth,
                death_date=death, birth_place=birth_place, notes=notes)


def build_demo_data():
    """4 поколения вымышленной семьи Тестовых + отработанные edge-case'ы."""
    P = []  # (данные, id выдаст БД; ниже связываем по локальному индексу)

    # Поколение 1 — прадеды/прабабки (частично неизвестные даты)
    P.append(person("Пётр", "Тестов", Gender.MALE, date(1928, 3, 14), date(1999, 11, 2),
                    middle="Иванович", birth_place="г. Вымысел",
                    notes="Ветеран труда. Телефон в заметке для проверки маскировки: +7 900 111-22-33"))
    P.append(person("Мария", "Тестова", Gender.FEMALE, date(1932, 7, 1), date(2010, 4, 18),
                    maiden="Примерова", birth_place="д. Образцово",
                    notes="Девичья фамилия Примерова. e-mail для маскировки: demo@example.com"))
    P.append(person("Николай", "Образцов", Gender.MALE, date(1930, 1, 20), date(1985, 9, 30),
                    notes="Год смерти предположителен."))
    P.append(person("Анна", "Образцова", Gender.FEMALE, date(1935, 5, 5),
                    maiden="Фиктивная", notes="Жива, дата смерти отсутствует — кейс живого человека."))

    # Поколение 2 — дети (у Тестовых сын и дочь; у Образцовых дочь в 2-м браке)
    P.append(person("Виктор", "Тестов", Gender.MALE, date(1955, 2, 10), date(2020, 12, 1),
                    middle="Петрович", notes="Инженер. Жил: ул. Демо, д. 7 (проверка маскировки адреса)."))
    P.append(person("Лидия", "Викторова", Gender.FEMALE, date(1957, 9, 23), date(2015, 6, 4),
                    maiden="Тестова", notes="Дочь Петра и Марии, вышла замуж за Викторова."))
    P.append(person("Сергей", "Викторов", Gender.MALE, date(1953, 11, 11),
                    middle="Николаевич", notes="Муж Лидии (Тестовой). Здоров жив."))
    P.append(person("Ольга", "Тестова", Gender.FEMALE, date(1960, 4, 2),
                    maiden="Тестова", notes="Второй ребёнок Петра и Марии, детей нет — тупиковая ветвь."))
    P.append(person("Дмитрий", "Образцов", Gender.MALE, None, None,
                    notes="Даты рождения неизвестны — кейс пропусков. Код для маскировки: 4321."))

    # Поколение 3
    P.append(person("Алексей", "Викторов", Gender.MALE, date(1980, 8, 15),
                    middle="Сергеевич", notes="Внук Петра Тестова (через Лидию)."))
    P.append(person("Елена", "Викторова", Gender.FEMALE, date(1983, 1, 30),
                    middle="Сергеевна", maiden="Викторова",
                    notes="Внучка Петра. После замужества фамилия Демидова (maiden=Викторова)."))
    P.append(person("Роман", "Демидов", Gender.MALE, date(1979, 6, 12),
                    notes="Муж Елены Викторовой. Пришёл со стороны."))

    # Поколение 4 — правнуки
    P.append(person("Иван", "Демидов", Gender.MALE, date(2005, 10, 25),
                    notes="Правнук Марии Тестовой. Учёный?"))
    P.append(person("Дарья", "Демидова", Gender.FEMALE, date(2008, 3, 8),
                    notes="Правнучка. Заметка с секретом для проверки запросов доступа к заметкам."))
    P.append(person("Максим", "Викторов", Gender.MALE, date(2010, 12, 31),
                    notes="Сын Алексея (мать неизвестна — кейс одиночного родителя)."))

    # Служебный «чужой» человек без связей — кейс изолированной карточки
    P.append(person("Изольда", "Безродная", Gender.FEMALE, date(1990, 2, 14),
                    notes="Без связей: кейс одинокой карточки в общем списке."))

    # (родитель, ребёнок) по индексам P[...]
    parent_of = [
        (0, 4), (1, 4),          # Пётр+Мария → Виктор
        (0, 6), (1, 6),          # → Лидия
        (0, 7), (1, 7),          # → Ольга
        (2, 8), (3, 8),          # Николай+Анна → Дмитрий
        (6, 9), (6, 10),         # Сергей (муж Лидии) → Алексей, Елена
        (10, 12), (10, 13),      # Елена → Иван, Дарья (отец Роман)
        (11, 12), (11, 13),      # Роман → Иван, Дарья
        (9, 14),                 # Алексей → Максим (одиночный родитель)
    ]
    # (муж/жена) по индексам
    spouse_of = [
        (0, 1), (2, 3),          # Пётр—Мария, Николай—Анна
        (6, 5),                  # Сергей—Лидия (id пары: PARENT_OF идёт от родителя)
        (10, 11),                # Елена—Роман
    ]
    return P, parent_of, spouse_of


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="снести существующие данные в этой БД")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    url = settings.database_url
    log.info("БД: %s", url)
    engine = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    existing = db.scalar(select(func.count()).select_from(Person))
    if existing and not args.force:
        log.error("В БД уже %d человек; используйте --force для пересоздания.", existing)
        return 1

    if args.force:
        db.execute(delete(Relationship))
        db.execute(delete(Person))
        db.commit()

    people, parent_of, spouse_of = build_demo_data()
    rows = [Person(**p) for p in people]
    db.add_all(rows)
    db.commit()

    def rel(a: int, b: int, rt: RelationType):
        db.add(Relationship(person_id=rows[a].id, related_person_id=rows[b].id, relation_type=rt))

    for a, b in parent_of:
        rel(a, b, RelationType.PARENT_OF)
    for a, b in spouse_of:
        rel(a, b, RelationType.SPOUSE_OF)
    db.commit()

    n = db.scalar(select(func.count()).select_from(Person))
    r = db.scalar(select(func.count()).select_from(Relationship))
    log.info("Готово: %d человек, %d связей (PARENT_OF + SPOUSE_OF).", n, r)
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
