from asyncio import sleep, run, create_task, Lock
from enum import IntEnum
from math import ceil, pi
from socket import gethostbyname
from typing import Callable, Optional

import numpy as np
from datek_agar_core.game import GameStatus, Organism, Bacteria as BacteriaModel
from datek_agar_core.network.client import UDPClient
from datek_agar_core.network.message import Message, MessageType
from datek_agar_core.universe import Universe
from datek_agar_core.utils import run_forever
from datek_agar_kivy.utils import SCALE, INITIAL_SIZE, INITIAL_CORRECTION, calculate_corrected_positions
from kivy.app import App
from kivy.core.window import Window
from kivy.core.window.window_sdl2 import WindowSDL
from kivy.properties import ObjectProperty, ObservableList, NumericProperty, StringProperty
from kivy.uix.label import Label
from kivy.uix.widget import Widget


DOUBLE_PI = 2 * pi


class ArrowKey(IntEnum):
    RIGHT = 79
    LEFT = 80
    DOWN = 81
    UP = 82


class Bacteria(Widget):
    hue = NumericProperty(0.5)
    name = StringProperty("")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._label = Label(text=self.name, pos=self.pos)
        self.add_widget(self._label)

    def change_pos(self, pos):
        self.pos = pos
        self._label.pos = pos


class HorizontalLine(Widget):
    pass


class VerticalLine(Widget):
    pass


_window: WindowSDL = Window


class GameStore:
    def __init__(self):
        self.player_id = 0
        self.positions: dict[str, np.ndarray] = {}
        self.speed_polar_coordinated = [0.0, 0.0]
        self.game_status: GameStatus = GameStatus()
        self.universe: Universe = ...

    @property
    def actual_position(self) -> Optional[np.ndarray]:
        bacteria = self.game_status.get_bacteria_by_id(self.player_id)
        return bacteria.position if bacteria else None

    def update_game_status(self, game_status: GameStatus):
        self.game_status = game_status


class OrganismCollection(Widget):
    def __init__(self, game_store: GameStore, **kwargs):
        super().__init__(**kwargs)
        self._game_store = game_store
        self._organisms: dict[str, Bacteria] = {}
        self._vector_array: np.ndarray = ...
        self._wanted_vector_array: np.ndarray = ...
        self._organism_sizes: np.ndarray = ...
        self._organism_radiuses: list = []
        self._init_registry()

    def update(self):
        self._init_registry()

        for bacteria in self._game_store.game_status.bacterias:
            if bacteria.id != self._game_store.player_id:
                self._register_organism(bacteria)

        for organism in self._game_store.game_status.organisms:
            self._register_organism(organism)

        if not self._organism_positions:
            return

        organism_positions = np.array(self._organism_positions, np.float32)

        origo_position = np.array(
            self._game_store.game_status.get_bacteria_by_id(self._game_store.player_id).position,
            np.float32
        )

        self._vector_array = self._game_store.universe.calculate_position_vector_array(
            origo_position,
            organism_positions
        )

        self._organism_radiuses = np.array(self._organism_radiuses, np.float32) * SCALE
        self._organism_sizes = self._organism_radiuses * 2

        self._vector_array *= SCALE
        self._vector_array[:] += np.array(_get_center(), np.float32)
        self._vector_array = calculate_corrected_positions(self._vector_array, self._organism_radiuses)

        found_1 = np.where(self._vector_array < [_window.width, _window.height])[0]
        found_2 = np.where(self._vector_array > -100)[0]
        indexes, index_counts = np.unique(np.concatenate((found_1, found_2)), return_counts=True)
        wanted_indexes = indexes[index_counts > 3]

        for index in wanted_indexes:
            self._update_organism(index)

        organisms_to_delete = set(self._organisms.keys()) - {self._index_organism_id_map[i] for i in wanted_indexes}

        for id_ in organisms_to_delete:
            self.remove_widget(self._organisms[id_])
            del self._organisms[id_]

    def _init_registry(self):
        self._organism_positions = []
        self._index_organism_id_map = {}
        self._organism_radiuses = []
        self._total_organism_collection = {}

    def _register_organism(self, organism: Organism):
        self._total_organism_collection[organism.id] = organism
        self._organism_positions.append(organism.position)
        self._index_organism_id_map[len(self._organism_positions) - 1] = organism.id
        self._organism_radiuses.append(organism.radius)

    def _update_organism(self, index):
        organism_id = self._index_organism_id_map[index]
        organism_widget = self._organisms.get(organism_id)
        pos = self._vector_array[index]
        pos = round(pos[0]), round(pos[1])
        size = round(self._organism_sizes[index])

        if organism_widget:
            organism_widget.change_pos(pos)
            organism_widget.size = [size, size]
            return

        organism = self._total_organism_collection[organism_id]
        hue = getattr(organism, "hue", 0.4)
        name = getattr(organism, "name", "")
        organism_widget = Bacteria(pos=pos, size=[size, size], hue=hue, name=name)
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
    player_name = ObjectProperty(None)
    player_name_label = ObjectProperty(None)
    host = ObjectProperty(None)
    host_label = ObjectProperty(None)
    port = ObjectProperty(None)
    port_label = ObjectProperty(None)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        _window.bind(on_key_down=self.on_key_down)
        _window.bind(on_resize=self.on_resize)

        self._lock = Lock()
        self._game_store = GameStore()
        self._grid = Grid(self._game_store)
        self._organism_collection = OrganismCollection(self._game_store)

        self._client: UDPClient = ...
        self._bacteria = Bacteria(
            pos=self.my_bacteria_position,
            size=[INITIAL_SIZE, INITIAL_SIZE],
            hue=0.5
        )

        self._message_handlers: dict[MessageType, Callable[[Message], ...]] = {
            MessageType.GAME_STATUS_UPDATE: self._handle_game_status_update,
            MessageType.CONNECT: self._handle_connect,
        }

    @property
    def my_bacteria(self) -> Optional[BacteriaModel]:
        return self._game_store.game_status.get_bacteria_by_id(self._game_store.player_id)

    @property
    def my_bacteria_position(self) -> tuple[int, int]:
        center = _get_center()

        if self._game_store.actual_position is None:
            return center[0] + INITIAL_CORRECTION, center[1] + INITIAL_CORRECTION

        correction = round(self.my_bacteria_size / 2)

        return (
            center[0] - correction,
            center[1] - correction,
        )

    @property
    def my_bacteria_size(self) -> int:
        return round(self.my_bacteria.radius * SCALE * 2)

    def on_resize(self, window: WindowSDL, width: int, height: int):
        self._bacteria.pos = self.my_bacteria_position

    async def update(self):
        async with self._lock:
            await self._update()

    async def _update(self):
        if self._game_store.actual_position is None:
            return

        self._grid.update()
        self._organism_collection.update()
        my_bacteria = self.my_bacteria
        size = self.my_bacteria_size
        self._bacteria.size = (size, size)
        self._bacteria.pos = self.my_bacteria_position
        self._bacteria.hue = my_bacteria.hue

    def connect(self):
        create_task(self._connect())

    def on_key_down(self, window: WindowSDL, keyboard: int, keycode: int, something, observables: ObservableList):
        if self._client is ... or not self._client.player_id:
            return

        try:
            key = ArrowKey(keycode)
        except ValueError:
            return

        speed = self._game_store.speed_polar_coordinated

        if key == ArrowKey.UP:
            speed[0] = min([1, speed[0] + 0.1])
        elif key == ArrowKey.DOWN:
            speed[0] = max([0, speed[0] - 0.1])
        elif key == ArrowKey.LEFT:
            speed[1] = max([(speed[1] + 0.05) % DOUBLE_PI])
        elif key == ArrowKey.RIGHT:
            speed[1] = min([(speed[1] - 0.05) % DOUBLE_PI])

        self._client.change_speed((speed[0], speed[1]))

    async def _connect(self):
        self._client = UDPClient(
            host=gethostbyname(self.host.text),
            port=int(self.port.text),
            handle_message=self._handle_message,
            ping_interval_sec=0.5,
            player_name=self.player_name.text
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

        self.add_widget(self._grid)
        self.add_widget(self._organism_collection)
        self.add_widget(self._bacteria)

        for widget in (
            self.button,
            self.player_name,
            self.player_name_label,
            self.host,
            self.host_label,
            self.port,
            self.port_label
        ):
            self.remove_widget(widget)


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


def _get_center() -> tuple[int, int]:
    return (
        round(_window.width / 2),
        round(_window.height / 2)
    )
