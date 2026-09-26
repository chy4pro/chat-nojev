"""Manual message-area geometry. Values are window points, never screen coordinates."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Calibration:
    window_width: float
    window_height: float
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self):
        values = tuple(self.__dict__.values())
        if (not all(math.isfinite(v) for v in values)
                or self.window_width <= 0 or self.window_height <= 0
                or self.x < 0 or self.y <= 0 or self.width < 80 or self.height < 60
                or self.x + self.width > self.window_width
                or self.y + self.height >= self.window_height):
            raise ValueError('请框选完整消息区，并排除标题和输入区。')

    @classmethod
    def parse(cls, value):
        return cls(*(float(v) for v in value.split(',')))

    def serialize(self):
        return ','.join(f'{v:.3f}' for v in self.__dict__.values())

    def matches(self, win):
        return (abs(win.w - self.window_width) < 1
                and abs(win.h - self.window_height) < 1)

    def rect(self):
        return (self.x / self.window_width, self.y / self.window_height,
                self.width / self.window_width, self.height / self.window_height)

    def screen_rect(self, win):
        return (win['x']+self.x, win['y']+self.y, self.width, self.height)


def validate_input_region(messages, editor):
    if (messages.window_width != editor.window_width
            or messages.window_height != editor.window_height
            or editor.y < messages.y+messages.height
            or editor.x < messages.x-8
            or editor.x+editor.width > messages.x+messages.width+8):
        raise ValueError('输入区应位于消息区下方，并完整框选编辑区，不含工具栏。')
