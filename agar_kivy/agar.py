from asyncio import sleep, run, create_task, get_running_loop
from enum import IntEnum
from random import random

from kivy.app import App
from kivy.core.window import Window
from kivy.core.window.window_sdl2 import WindowSDL
from kivy.properties import ObjectProperty, ObservableList, NumericProperty
from kivy.uix.widget import Widget

from agar_core.network.client import UDPClient
from agar_core.network.message import Message, MessageType
from agar_core.universe import Universe
from agar_core.utils import run_forever


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


class Grid(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        window: WindowSDL = Window
        window.bind(on_resize=self.on_resize)
        self.size = window.size
        self._line = HorizontalLine(y=round(self.width / 2), size=[self.width, 2])
        self.add_widget(self._line)

    def on_resize(self, window: WindowSDL, width: int, height: int):
        self.size = window.size
        self._line.size = [self.width, 2]


class Game(Widget):
    button = ObjectProperty(None)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        window: WindowSDL = Window
        window.bind(on_key_down=self.on_key_down)
        self.add_widget(Grid())

        self._loop = get_running_loop()
        self._client: UDPClient = ...
        self._position = [0, 0]
        self._positions: dict[str, tuple[int, int]] = {}

        self._speed = [0, 0]
        self._bacteria = Bacteria(pos=self._position, size=[20, 20], hue=0.5)
        self._bacterias: dict[str, Bacteria] = {}

        self.add_widget(self._bacteria)

    async def update(self):
        center_x = round(self.width / 2)
        center_y = round(self.height / 2)

        self._bacteria.pos = [
            center_x + self._position[0] * Universe.DISTANCE_SCALE,
            center_y + self._position[1] * Universe.DISTANCE_SCALE
        ]

        for id_, position in self._positions.items():
            if id_ == self._client.player_id:
                continue

            bacteria_widget = self._bacterias.get(id_)
            pos = [
                center_x + position[0] * Universe.DISTANCE_SCALE,
                center_y + position[1] * Universe.DISTANCE_SCALE
            ]

            if bacteria_widget:
                bacteria_widget.pos = pos
                continue

            bacteria_widget = Bacteria(pos=pos, size=[20, 20], hue=random())
            self.add_widget(bacteria_widget)
            self._bacterias[id_] = bacteria_widget

    def connect(self):
        self._loop.create_task(self._connect())

    def on_key_down(self, window: WindowSDL, keyboard: int, keycode: int, something, observables: ObservableList):
        if self._client is ... or not self._client.player_id:
            return

        key = ArrowKey(keycode)

        if key == ArrowKey.UP:
            self._speed[1] = min([Universe.MAX_SPEED_PERCENT, self._speed[1] + 1])
        elif key == ArrowKey.DOWN:
            self._speed[1] = max([-Universe.MAX_SPEED_PERCENT, self._speed[1] - 1])
        elif key == ArrowKey.LEFT:
            self._speed[0] = max([-Universe.MAX_SPEED_PERCENT, self._speed[0] - 1])
        elif key == ArrowKey.RIGHT:
            self._speed[0] = min([Universe.MAX_SPEED_PERCENT, self._speed[0] + 1])

        self._client.change_speed((self._speed[0], self._speed[1]))
        print(self._speed)

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
        if message.type == MessageType.GAME_STATUS_UPDATE:
            if not self._client.player_id:
                return

            self._game_status = message.game_status
            self._position = message.game_status.bacterias[self._client.player_id].position

            for bacteria in self._game_status.bacterias.values():
                if bacteria.id == self._client.player_id:
                    continue

                self._positions[bacteria.id] = bacteria.position


class AgarApp(App):
    def build(self):
        game = Game()
        refresh_frequency = 1 / 100

        @run_forever
        async def update_game():
            await game.update()
            await sleep(refresh_frequency)

        create_task(update_game())

        return game


def main():
    run(AgarApp().async_run(async_lib="asyncio"))
