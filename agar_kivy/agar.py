from asyncio import sleep, run, create_task, Lock
from enum import IntEnum
from math import ceil
from typing import Callable, Optional

from agar_core.game import GameStatus
from agar_core.network.client import UDPClient
from agar_core.network.message import Message, MessageType
from agar_core.utils import run_forever
from kivy.app import App
from kivy.core.window import Window
from kivy.core.window.window_sdl2 import WindowSDL
from kivy.properties import ObjectProperty, ObservableList, NumericProperty
from kivy.uix.widget import Widget

SCALE = 60
VIEW_DISTANCE_MODIFIER = 40


class ArrowKey(IntEnum):
    RIGHT = 79
    LEFT = 80
    DOWN = 81
    UP = 82


class Bacteria(Widget):
    hue = NumericProperty(0.5)


class HorizontalLine(Widget):
    pass


class VerticalLine(Widget):
    pass


class GameStore:
    def __init__(self):
        self.player_id = ""
        self.positions: dict[str, tuple[int, int]] = {}
        self.speed_percentage = [0, 0]
        self.game_status: GameStatus = GameStatus()

    @property
    def actual_position(self) -> Optional[tuple[int, int]]:
        bacteria = self.game_status.bacterias.get(self.player_id)
        return bacteria.position if bacteria else None

    @property
    def view_distance(self) -> float:
        return self.game_status.bacterias[self.player_id].radius * VIEW_DISTANCE_MODIFIER

    def update_game_status(self, game_status: GameStatus):
        self.game_status = game_status


class BacteriaCollection(Widget):
    def __init__(self, game_store: GameStore, **kwargs):
        super().__init__(**kwargs)
        self._game_store = game_store
        self._bacterias: dict[str, Bacteria] = {}

    def update(self):
        window: WindowSDL = Window
        for id_, bacteria in self._game_store.game_status.bacterias.items():
            if id_ == self._game_store.player_id:
                continue

            bacteria_widget = self._bacterias.get(id_)
            relative_position = (
                bacteria.position[0] - self._game_store.actual_position[0],
                bacteria.position[1] - self._game_store.actual_position[1],
            )

            pos = [window.size[0] / 2, window.size[1] / 2]
            pos[0] += relative_position[0] * SCALE
            pos[1] += relative_position[1] * SCALE

            if bacteria_widget:
                bacteria_widget.pos = pos
                continue

            radius = bacteria.radius * SCALE
            bacteria_widget = Bacteria(pos=pos, size=[radius, radius], hue=bacteria.hsv[0])
            self.add_widget(bacteria_widget)
            self._bacterias[id_] = bacteria_widget


class Grid(Widget):
    LINE_SPACING = 150

    def __init__(self, game_store: GameStore, **kwargs):
        super().__init__(**kwargs)
        window: WindowSDL = Window
        window.bind(on_resize=self.on_resize)
        self.size = window.size

        self._virtual_size = self.size
        self._vertical_lines = []
        self._horizontal_lines = []
        self._game_store = game_store
        self._actual_position = (0, 0)
        self._previous_position = (0, 0)
        self._init_lines()

    def on_resize(self, window: WindowSDL, width: int, height: int):
        self.size = window.size
        for line in self._horizontal_lines + self._vertical_lines:
            self.remove_widget(line)

        self._vertical_lines = []
        self._horizontal_lines = []

        self._init_lines()

    def update(self):
        self._previous_position = self._actual_position
        self._actual_position = self._game_store.actual_position

        vector = (
            self._actual_position[0] - self._previous_position[0],
            self._actual_position[1] - self._previous_position[1],
        )

        for line in self._vertical_lines:
            line.x = (line.x - (vector[0] * SCALE)) % self._virtual_size[0]

        for line in self._horizontal_lines:
            line.y = (line.y - (vector[1] * SCALE)) % self._virtual_size[1]

    def _init_lines(self):
        self._virtual_size = [
            ceil(self.width / self.LINE_SPACING) * self.LINE_SPACING,
            ceil(self.height / self.LINE_SPACING) * self.LINE_SPACING,
        ]

        self._horizontal_lines = [
            HorizontalLine(y=y, size=[self.width, 2])
            for y in range(0, self.height, self.LINE_SPACING)
        ]

        self._vertical_lines = [
            VerticalLine(x=x, size=[2, self.height])
            for x in range(0, self.width, self.LINE_SPACING)
        ]

        for line in self._horizontal_lines + self._vertical_lines:
            self.add_widget(line)


class Game(Widget):
    button = ObjectProperty(None)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        window: WindowSDL = Window
        window.bind(on_key_down=self.on_key_down)
        window.bind(on_resize=self.on_resize)

        self._lock = Lock()
        self._game_store = GameStore()
        self._grid = Grid(self._game_store)
        self._bacteria_collection = BacteriaCollection(self._game_store)
        self.add_widget(self._grid)
        self.add_widget(self._bacteria_collection)

        self._client: UDPClient = ...

        self._bacteria = Bacteria(
            pos=[round(window.size[0] / 2), round(window.size[1] / 2)],
            size=[30, 30],
            hue=0.5
        )

        self._message_handlers: dict[MessageType, Callable[[Message], ...]] = {
            MessageType.GAME_STATUS_UPDATE: self._handle_game_status_update,
            MessageType.CONNECT: self._handle_connect,
        }

        self.add_widget(self._bacteria)

    def on_resize(self, window: WindowSDL, width: int, height: int):
        self._bacteria.pos = [round(window.size[0] / 2), round(window.size[1] / 2)]

    async def update(self):
        async with self._lock:
            await self._update()

    async def _update(self):
        if not self._game_store.actual_position:
            return

        self._grid.update()
        self._bacteria_collection.update()
        my_bacteria = self._game_store.game_status.bacterias.get(self._game_store.player_id)
        radius = my_bacteria.radius * SCALE
        self._bacteria.size = (radius, radius)
        self._bacteria.hue = my_bacteria.hsv[0]

    def connect(self):
        create_task(self._connect())

    def on_key_down(self, window: WindowSDL, keyboard: int, keycode: int, something, observables: ObservableList):
        if self._client is ... or not self._client.player_id:
            return

        try:
            key = ArrowKey(keycode)
        except ValueError:
            return

        speed = self._game_store.speed_percentage

        if key == ArrowKey.UP:
            speed[1] = min([1, speed[1] + 0.1])
        elif key == ArrowKey.DOWN:
            speed[1] = max([-1, speed[1] - 0.1])
        elif key == ArrowKey.LEFT:
            speed[0] = max([-1, speed[0] - 0.1])
        elif key == ArrowKey.RIGHT:
            speed[0] = min([1, speed[0] + 0.1])

        self._client.change_speed((speed[0], speed[1]))

    async def _connect(self):
        self._client = UDPClient(
            host="127.0.0.1",
            port=9999,
            handle_message=self._handle_message,
            ping_frequency_sec=0.5,
            player_name="Atti"
        )
        self._client.start()
        self.remove_widget(self.button)

    async def _handle_message(self, message: Message):
        handler = self._message_handlers.get(message.type)
        if handler:
            await handler(message)

    async def _handle_game_status_update(self, message: Message):
        if not self._client.player_id:
            return

        if self._lock.locked():
            return

        async with self._lock:
            self._game_store.update_game_status(message.game_status)

    async def _handle_connect(self, message: Message):
        self._game_store.player_id = message.bacteria_id


class AgarApp(App):
    def build(self):
        game = Game()
        refresh_frequency = 1 / 60

        @run_forever
        async def update_game():
            await game.update()
            await sleep(refresh_frequency)

        create_task(update_game())

        return game


def main():
    run(AgarApp().async_run(async_lib="asyncio"))
