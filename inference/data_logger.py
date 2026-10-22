import csv
import os
from datetime import datetime

from app_paths import data_path

# What a socket's columns hold when it said nothing during a frame. Read back
# as a missing value by pandas and by Excel, and unlike 0 it cannot be mistaken
# for a reading: a sensor resting at zero and a sensor that is not there are
# different things.
MISSING = "NaN"


class DataLogger:
    """Writes one CSV per recording session, a frame at a time.

    A frame is one pass of the source over every bound socket. Samples are
    collected until the frame turns over and then written as a fixed block:
    one row per socket, always in the same order, whether or not that socket
    reported.

    Why not write each sample as it arrives, which is simpler: the file then
    starts wherever the operator's click landed. Pressing Record halfway
    through a pass gave a file beginning at, say, B2, and every 12-row chunk of
    it straddled two passes. Recording used to be held back until socket A1
    arrived to avoid exactly that - which in turn meant a session with A1
    unbound or unplugged recorded nothing at all, eleven working sensors
    included.

    Buffering the frame settles both: the rotation the samples arrive in stops
    mattering because the block is written in socket order regardless, and a
    socket that never reports takes its place in every frame as MISSING rather
    than shifting the ones after it.
    """

    HEADER = ["timestamp", "sensor_id", "short_id", "volume", "raw_bz",
              "force_n", "gram", "displacement_mm", "displacement_um"]

    # Newtons to gram-force, from g = 9.80665
    GF_PER_N = 101.9716

    def __init__(self, folder="outputs"):
        self.output_dir = data_path(folder)
        self.is_logging = False
        self.current_file = None
        self.current_volume = "0"  # Default volume value

        # The file is held open for the length of a session. Re-opening it per
        # row cost about 0.3 ms against 0.002 ms for a write on a handle that
        # is already open - 150x, paid on the GUI thread for every sample of
        # every sensor. Frames are flushed as they are written, so a crash
        # still leaves everything up to the last complete frame on disk.
        self._handle = None
        self._writer = None

        # The frame layout: every socket that has a card, in the order their
        # rows are written. Set by the application when the bindings change.
        self._sockets = []
        self._frame = {}
        # Frame anchoring - see log().
        self._anchor = None
        self._seen = set()
        self._waiting = True
        self._frame_size = 0        # how big a complete frame has proved to be

    # --- setup -------------------------------------------------------------

    def set_sockets(self, sockets):
        """The sockets a frame is made of, in the order they are written."""
        self._sockets = list(sockets)

    def set_volume(self, value):
        """Updates the volume value to be logged with each data point."""
        self.current_volume = str(value)

    def create_new_session(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.current_file = os.path.join(self.output_dir, f"log_{timestamp}.csv")

        try:
            if not os.path.exists(self.output_dir):
                os.makedirs(self.output_dir)

            self._close_handle()
            self._handle = open(self.current_file, mode='w', newline='')
            self._writer = csv.writer(self._handle)
            self._writer.writerow(self.HEADER)
            self._handle.flush()

            self._frame = {}
            self._anchor = None
            self._seen = set()
            self._waiting = True
            self._frame_size = 0
            self.is_logging = True
            print(f"[Logger]: Recording to {os.path.basename(self.current_file)} "
                  f"({len(self._sockets) or 'unknown'} sockets per frame)")
        except Exception as e:
            print(f"[Error]: Could not initialize logger: {e}")

    # --- recording ---------------------------------------------------------

    def log(self, sensor_id, short_id, raw_bz, force_n, displacement_mm=0.0):
        """Take one sample into the frame being built.

        Frames are anchored to one socket - the first one in the layout that is
        actually reporting - and a frame ends when that socket comes round
        again. Anchoring rather than waiting for "some socket repeated" matters:
        pressing Record part-way through a pass and then cutting on the first
        repeat gives a full twelve rows made of the tail of one pass and the
        head of the next, which looks complete and is not.

        Until the anchor is known, and then until it next arrives, samples are
        discarded. That costs up to two passes - about 80 ms - and is what
        waiting for A1 used to do, without A1 having to be the one that works.
        """
        if not self.is_logging or self._writer is None:
            return

        if self._anchor is None:
            # Watch one full pass to see which sockets are reporting at all.
            if short_id in self._seen:
                self._anchor = self._pick_anchor()
                self._seen = set()
                print(f"[Logger]: frames anchored on {self._anchor}")
            else:
                self._seen.add(short_id)
                return

        if self._waiting:
            if short_id != self._anchor:
                return                      # still inside the partial pass
            self._waiting = False

        if short_id == self._anchor and self._frame:
            self._write_frame(self._frame)
            self._frame = {}

        self._frame[short_id] = {
            "timestamp": datetime.now().strftime("%H:%M:%S.%f")[:-3],
            "sensor_id": sensor_id,
            "raw_bz": raw_bz,
            "force_n": force_n,
            "displacement_mm": displacement_mm,
        }

    def _pick_anchor(self):
        """The first socket of the layout that actually reported.

        With every socket working this is the layout's first, so frames begin
        where they always did. With that one unbound or dead it is the next one
        along, which is what lets a run carry on with eleven sockets, or one.
        """
        for socket in self._sockets:
            if socket in self._seen:
                return socket
        return sorted(self._seen)[0]        # no layout set; fall back

    def _write_frame(self, frame):
        """One row per socket, in socket order, NaN where it said nothing."""
        if self._writer is None:
            return

        # A socket that has never been bound still has to be written if it is
        # part of the layout; one that is not in the layout at all (a socket
        # bound after recording started, say) is appended so its data is not
        # thrown away.
        sockets = list(self._sockets) or sorted(frame)
        for short_id in frame:
            if short_id not in sockets:
                sockets.append(short_id)

        stamp = next(iter(frame.values()))["timestamp"]

        for short_id in sockets:
            sample = frame.get(short_id)
            if sample is None:
                self._writer.writerow([stamp, MISSING, short_id,
                                       self.current_volume,
                                       MISSING, MISSING, MISSING,
                                       MISSING, MISSING])
                continue

            force_n = sample["force_n"]
            displacement_mm = sample["displacement_mm"]
            self._writer.writerow([
                sample["timestamp"],
                sample["sensor_id"],
                short_id,
                self.current_volume,
                round(sample["raw_bz"], 2),
                round(force_n, 4),
                round(force_n * self.GF_PER_N, 2),
                round(displacement_mm, 5),
                round(displacement_mm * 1000.0, 2),
            ])

        self._handle.flush()
        self._frame_size = max(self._frame_size, len(frame))

    # --- teardown ----------------------------------------------------------

    def _close_handle(self):
        if self._handle is not None:
            try:
                self._handle.close()
            except Exception:
                pass
        self._handle = None
        self._writer = None

    def stop_session(self):
        """Close the session, keeping a last pass that happens to be complete.

        The frame in hand has not been written because its anchor never came
        round again - but stopping does not make it incomplete. If it holds as
        many sockets as the frames before it, it is a whole pass and is kept;
        if it is short, the operator stopped part-way through one and it is
        dropped, the same artefact as at the start.
        """
        if self._frame and len(self._frame) >= self._frame_size > 0:
            self._write_frame(self._frame)
        self._frame = {}
        self.is_logging = False
        self._close_handle()
        self.current_file = None
        self._frame = {}
        self._anchor = None
        self._seen = set()
        self._waiting = True
        print("[Logger]: Session closed.")
