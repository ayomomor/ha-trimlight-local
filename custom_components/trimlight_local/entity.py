from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TrimlightCoordinator
from .trimlight import DailySchedule


class TrimlightEntity(CoordinatorEntity[TrimlightCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: TrimlightCoordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        base_id = entry.unique_id or entry.entry_id
        self._attr_unique_id = f"{base_id}_{key}"
        firmware = coordinator.client.firmware
        mcu_version = firmware.mcu_version if firmware is not None else None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, base_id)},
            name=entry.title,
            manufacturer="Trimlight",
            model="Edge",
            sw_version=firmware.version if firmware is not None else None,
            hw_version=f"MCU {mcu_version:#06x}" if mcu_version is not None else None,
        )


class TrimlightDailyScheduleEntity(TrimlightEntity):
    """One setting of daily schedule 1 or 2 (index 0 or 1)."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: TrimlightCoordinator, entry: ConfigEntry, index: int, setting: str) -> None:
        super().__init__(coordinator, entry, f"daily_schedule_{index + 1}_{setting}")
        self._index = index
        self._attr_translation_key = f"daily_schedule_{setting}"
        self._attr_translation_placeholders = {"number": str(index + 1)}

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None and len(self.coordinator.data.daily) > self._index

    @property
    def _schedule(self) -> DailySchedule | None:
        data = self.coordinator.data
        return data.daily[self._index] if data is not None and len(data.daily) > self._index else None

    async def _async_change(self, **changes: object) -> None:
        await self.coordinator.async_set_daily_schedule(self._index, **changes)
