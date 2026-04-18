"""
app/bot/states.py — FSM-состояния.
"""
from aiogram.fsm.state import State, StatesGroup


class LobbyCreation(StatesGroup):
    choosing_mode  = State()


class LobbyJoin(StatesGroup):
    entering_code  = State()


class GamePlay(StatesGroup):
    task_active     = State()   # ход активного игрока — выбирает правда/действие
    entering_custom = State()   # игрок вводит своё задание
    answering_truth = State()   # игрок печатает/записывает ответ на правду
    uploading_dare  = State()   # игрок загружает подтверждение действия
    waiting_vote    = State()   # ждём голосов за выполнение
    waiting_viewers = State()   # ждём просмотра медиа (старый флоу)
