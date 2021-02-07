from asyncio import sleep, run, create_task, Lock
from enum import IntEnum
from math import ceil
from typing import Callable, Optional

import numpy as np
from agar_core.game import GameStatus, Organism
from agar_core.network.client import UDPClient
from agar_core.network.message import Message, MessageType
from agar_core.universe import Universe
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

    def __init__(self, **kwargs):
        pos = kwargs["pos"]
        size = kwargs["size"]

        pos = [
            pos[0] - size[0],
            pos[1] - size[1],
        ]

        kwargs["pos"] = pos
        kwargs["size"] = size
        super().__init__(**kwargs)


class HorizontalLine(Widget):
    pass


class VerticalLine(Widget):
    pass


_window: WindowSDL = Window


class GameStore:
    def __init__(self):
        self.player_id = ""
        self.positions: dict[str, np.ndarray] = {}
        self.speed_percentage = [0, 0]
        self.game_status: GameStatus = GameStatus()
        self.universe: Universe = ...

    @property
    def actual_position(self) -> Optional[np.ndarray]:
        bacteria = self.game_status.bacterias.get(self.player_id)
        return bacteria.position if bacteria else None

    @property
    def view_distance(self) -> float:
        return self.game_status.bacterias[self.player_id].radius * VIEW_DISTANCE_MODIFIER

    def update_game_status(self, game_status: GameStatus):
        self.game_status = game_status


class OrganismCollection(Widget):
    def __init__(self, game_store: GameStore, **kwargs):
        super().__init__(**kwargs)
        self._game_store = game_store
        self._organisms: dict[str, Bacteria] = {}
        self._organism_positions = []
        self._id_position_map = {}
        self._total_organism_collection = {}
        self._vector_array: np.ndarray = ...
        self._position_array: np.ndarray = ...

    def update(self):
        self._organism_positions = []
        self._id_position_map = {}

        for bacteria in self._game_store.game_status.bacterias.values():
            if bacteria.id != self._game_store.player_id:
                self._register_organism(bacteria)

        for organism in self._game_store.game_status.organisms:
            self._register_organism(organism)

        if not self._organism_positions:
            return

        organism_positions = np.array(self._organism_positions, np.float32)
        origo_position = self._game_store.game_status.bacterias[self._game_store.player_id].position
        origo_array = np.ndarray(organism_positions.shape, np.float32)
        origo_array[:] = [origo_position[0], origo_position[1]]

        self._vector_array = self._game_store.universe.calculate_position_vector_array(
            origo_array,
            organism_positions
        )

        self._position_array = np.ndarray(organism_positions.shape, np.float32)
        self._position_array[:] = np.array(_window.size, np.float32) / 2
        self._position_array += self._vector_array * SCALE

        for id_ in self._id_position_map.keys():
            self._update_organism(id_)

    def _register_organism(self, organism: Organism):
        self._total_organism_collection[organism.id] = organism
        self._organism_positions.append(organism.position)
        self._id_position_map[organism.id] = len(self._organism_positions) - 1

    def _update_organism(self, organism_id):
        organism_widget = self._organisms.get(organism_id)
        index = self._id_position_map[organism_id]
        pos = self._position_array[index]
        pos = [int(pos[0]), int(pos[1])]

        if pos[0] > _window.size[0] or pos[1] > _window.size[1]:
            if not organism_widget:
                return

            self.remove_widget(organism_widget)
            del self._organisms[organism_id]
            return

        if organism_widget:
            organism_widget.pos = pos
            return

        organism = self._total_organism_collection[organism_id]
        hsv = getattr(organism, "hsv", [0.4])
        radius = organism.radius * SCALE
        organism_widget = Bacteria(pos=pos, size=[radius, radius], hue=hsv[0])
        self.add_widget(organism_widget)
        self._organisms[organism_id] = organism_widget


class Grid(Widget):
    LINE_SPACING = 150

    def __init__(self, game_store: GameStore, **kwargs):
        super().__init__(**kwargs)
        _window.bind(on_resize=self.on_resize)
        self.size = _window.size

        self._virtual_size = self.size
        self._vertical_lines = []
        self._horizontal_lines = []
        self._game_store = game_store
        self._actual_position = np.zeros(2, np.float32)
        self._previous_position = np.zeros(2, np.float32)
        self._init_lines()
        self._vector = np.zeros(2, np.float32)

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
        vector = self._game_store.universe.calculate_position_vector_array(
            self._previous_position,
            self._actual_position
        ) * SCALE

        self._vector += vector

        if abs(self._vector[0]) >= 1:
            rounded = round(self._vector[0])
            for line in self._vertical_lines:
                line.x = (line.x - rounded) % self._virtual_size[0]

            self._vector[0] -= rounded

        if abs(self._vector[1]) >= 1:
            rounded = round(self._vector[1])
            for line in self._horizontal_lines:
                line.y = (line.y - rounded) % self._virtual_size[1]

            self._vector[1] -= rounded

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
        _window.bind(on_key_down=self.on_key_down)
        _window.bind(on_resize=self.on_resize)

        self._lock = Lock()
        self._game_store = GameStore()
        self._grid = Grid(self._game_store)
        self._organism_collection = OrganismCollection(self._game_store)
        self.add_widget(self._grid)
        self.add_widget(self._organism_collection)

        self._client: UDPClient = ...

        self._bacteria = Bacteria(
            pos=[round(_window.size[0] / 2), round(_window.size[1] / 2)],
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
        if self._game_store.actual_position is None:
            return

        self._grid.update()
        self._organism_collection.update()
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
        self._game_store.universe = Universe(
            world_size=message.world_size,
            total_nutrient=message.total_nutrient,
        )
        self.remove_widget(self.button)


class AgarApp(App):
    def build(self):
        game = Game()
        refresh_frequency = 1 / 35

        @run_forever
        async def update_game():
            await game.update()
            await sleep(refresh_frequency)

        create_task(update_game())

        return game


def main():
    run(AgarApp().async_run(async_lib="asyncio"))
