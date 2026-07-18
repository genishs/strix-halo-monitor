"""Job detection and log parsing (DESIGN §2.2 C).

Pure, HW-independent: given log text + a UnitRef + the current time, produce a
:class:`~halo_monitor.model.JobState`. JSON status line preferred, log-phrase regex
as fallback. This is the highest-value area for unit testing.
"""
