#!/bin/python

import aaf2
import os
import sys
import wave
import urllib.parse
import urllib.request
import pprint
import json

have_reaper = True
have_tk = True

try:
    from reaper_python import *
except ModuleNotFoundError:
    have_reaper = False

if have_reaper:
    try:
        sys.argv=["Main"]
        import tkinter
        import tkinter.ttk
    except Exception:
        have_tk = False
else:
    have_tk = False

[NOTICE, WARNING, ERROR, NONE] = range(4)
log_level = WARNING

def log(message, level=NOTICE):
    if log_level > level: return
    if have_reaper:
        RPR_ShowConsoleMsg(message + "\n")
    else:
        print(message)



class ReaperInterface:

    def __init__(self):
        self.insertion_track = None

    def select_aaf(self):
        ok, filename, _, _ = RPR_GetUserFileNameForRead("", "Import AAF", ".aaf")
        if not ok: return None
        return filename

    def get_project_directory(self):
        directory, _ = RPR_GetProjectPath("", 512)
        return directory

    def create_track(self, name):
        track_index = RPR_GetNumTracks()
        RPR_InsertTrackAtIndex(track_index, False)
        track = RPR_GetTrack(0, track_index)
        RPR_GetSetMediaTrackInfo_String(track, "P_NAME", name, True)
        return track

    def set_track_volume(self, track, volume):
        RPR_SetMediaTrackInfo_Value(track, "D_VOL", volume)

    def set_track_volume_envelope(self, track, volume_data):
        RPR_SetOnlyTrackSelected(track)
        RPR_Main_OnCommand(40406, 0)  # ReaSlang for "toggle volume envelope visible"
        envelope = RPR_GetTrackEnvelopeByName(track, "Volume")
        for point in volume_data:
            value = RPR_ScaleToEnvelopeMode(1, point["value"])
            RPR_InsertEnvelopePoint(envelope, point["time"], value, 0, 0.0, False, True)
        RPR_Envelope_SortPoints(envelope)

    def set_track_panning(self, track, panning):
        RPR_SetMediaTrackInfo_Value(track, "D_PAN", panning)

    def set_track_panning_envelope(self, track, panning_data):
        RPR_SetOnlyTrackSelected(track)
        RPR_Main_OnCommand(40407, 0)  # Toggle pan envelope visible
        envelope = RPR_GetTrackEnvelopeByName(track, "Pan")
        for point in panning_data:
            RPR_InsertEnvelopePoint(envelope, point["time"], point["value"], 0, 0.0, False, True)
        RPR_Envelope_SortPoints(envelope)

    def create_item(self, track, src, offset, pos, dur):
        RPR_SetOnlyTrackSelected(self.insertion_track)
        RPR_MoveEditCursor(-1000, False)
        RPR_InsertMedia(src, 0)
        item = RPR_GetTrackMediaItem(self.insertion_track, 0)
        RPR_MoveMediaItemToTrack(item, track)
        RPR_SetMediaItemInfo_Value(item, "D_POSITION", pos)
        RPR_SetMediaItemInfo_Value(item, "D_LENGTH", dur)
        take = RPR_GetMediaItemTake(item, 0)
        RPR_SetMediaItemTakeInfo_Value(take, "D_STARTOFFS", offset)
        return item

    def set_item_fades(self, item, fadein=None, fadeout=None, fadeintype=0, fadeouttype=0):
        if fadein:
            RPR_SetMediaItemInfo_Value(item, "D_FADEINLEN", fadein)
            RPR_SetMediaItemInfo_Value(item, "C_FADEINSHAPE", fadeintype)
        if fadeout:
            RPR_SetMediaItemInfo_Value(item, "D_FADEOUTLEN", fadeout)
            RPR_SetMediaItemInfo_Value(item, "C_FADEOUTSHAPE", fadeouttype)

    def set_item_volume(self, item, volume):
        RPR_SetMediaItemInfo_Value(item, "D_VOL", volume)

    def create_marker(self, pos, name="", colour=None):
        colour_code = 0
        if colour:
            colour_code = RPR_ColorToNative(colour["r"], colour["g"], colour["b"]) | 0x1000000
        RPR_AddProjectMarker2(0, False, pos, 0.0, name, 0, colour_code)

    def build_project(self, data):
        self.insertion_track = self.create_track("Insertion")

        for track_data in data["tracks"]:
            track = self.create_track(track_data["name"])

            if "volume" in track_data:
                self.set_track_volume(track, track_data["volume"])
            if "panning" in track_data:
                self.set_track_panning(track, track_data["panning"])
            if "volume_envelope" in track_data:
                self.set_track_volume_envelope(track, track_data["volume_envelope"])
            if "panning_envelope" in track_data:
                self.set_track_panning_envelope(track, track_data["panning_envelope"])

            if "items" not in track_data: continue
            for item_data in track_data["items"]:
                item = self.create_item(
                    track,
                    item_data["source"],
                    item_data["offset"],
                    item_data["position"],
                    item_data["duration"]
                )
                if "fadein" in item_data or "fadeout" in item_data:
                    self.set_item_fades(
                        item,
                        item_data.get("fadein", None),
                        item_data.get("fadeout", None),
                        item_data.get("fadeintype", 0),
                        item_data.get("fadeouttype", 0)
                    )
                if "volume" in item_data:
                    self.set_item_volume(item, item_data["volume"])

        for marker_data in data["markers"]:
            self.create_marker(marker_data["position"], marker_data.get("name", ""), marker_data.get("colour", None))

        RPR_DeleteTrack(self.insertion_track)
        self.insertion_track = None



class AAFInterface:

    def __init__(self):
        self.aaf = None
        self.encoder = ""
        self.aaf_directory = ""
        self.essence_data = {}

    def open(self, filename):
        try:
            self.aaf = aaf2.open(filename, "r")
        except Exception:
            log("Could not open AAF file.", ERROR)
            return False
        try:
            self.encoder = self.aaf.header["IdentificationList"][0]["ProductName"].value
        except Exception:
            log("Unable to find file encoder", WARNING)
        self.aaf_directory = os.path.abspath(os.path.dirname(filename))
        self.essence_data = {}
        return True

    def build_wav(self, fname, data, depth=16, rate=48000, channels=1):
        with wave.open(fname, "wb") as f:
            f.setnchannels(channels)
            f.setsampwidth(int(depth / 8))
            f.setframerate(rate)
            f.writeframesraw(data)
            f.close()

    def aafrational_value(self, rational):
        return rational.numerator / rational.denominator

    def get_point_list(self, varying, duration):
        data = []
        for point in varying["PointList"]:
            data.append({
                "time": point.time * duration,
                "value": point.value
            })
        return data

    def get_linked_essence(self, mob):
        try:
            url = mob.descriptor.locator.pop()["URLString"].value
            # file:///C%3a/Users/user/My%20video.mp4
            url = urllib.parse.urlparse(url)
            url = url.netloc + url.path
            # /C%3a/Users/user/My%20video.mp4
            url = urllib.parse.unquote(url)
            # /C:/Users/user/My video.mp4
            url = urllib.request.url2pathname(url)
            # C:\\Users\\user\\My video.mp4

            # If the AAF was built on another computer,
            # chances are the paths will differ.
            # Typically the source files are in the same directory as the AAF.
            if not os.path.isfile(url):
                local = os.path.join(self.aaf_directory, os.path.basename(url))
                if os.path.isfile(local):
                    url = local
            return url

        except Exception:
            log("Error retrieving file url for %s" % mob.name, WARNING)
            return ""

    def extract_embedded_essence(self, mob, filename):
        log("Extracting essence %s..." % filename)
        stream = mob.essence.open()
        data = stream.read()
        stream.close()

        meta = mob.descriptor
        data_fmt = meta["ContainerFormat"].value.name if "ContainerFormat" in meta else ""
        if data_fmt == "MXF":
            sample_depth = meta["QuantizationBits"].value
            sample_rate = meta["SampleRate"].value
            sample_rate = self.aafrational_value(sample_rate)
            self.build_wav(filename, data, sample_depth, sample_rate)
        else:
            with open(filename, "wb") as f:
                f.write(data)
                f.close()

        return filename

    def extract_essence(self, target, callback):
        for master_mob in self.aaf.content.mastermobs():
            self.essence_data[master_mob.name] = {}
            for slot in master_mob.slots:

                if isinstance(slot.segment, aaf2.components.Sequence):
                    source_mob = None
                    for component in slot.segment.components:
                        if isinstance(component, aaf2.components.SourceClip):
                            source_mob = component.mob
                            break
                    else:
                        self.essence_data[master_mob.name][slot.slot_id] = ""
                        log("Cannot find essence for %s slot %d" % (master_mob.name, slot.slot_id), WARNING)
                elif isinstance(slot.segment, aaf2.components.SourceClip):
                    source_mob = slot.segment.mob

                if slot.segment.media_kind == "Picture":
                    # Video files cannot be embedded in the AAF.
                    self.essence_data[master_mob.name][slot.slot_id] = self.get_linked_essence(source_mob)
                    continue
                if source_mob.essence:
                    filename = os.path.join(target, master_mob.name + slot.name + ".wav")
                    if callback:
                        callback("Extracting %s..." % (master_mob.name + slot.name + ".wav"))
                    self.essence_data[master_mob.name][slot.slot_id] = self.extract_embedded_essence(source_mob, filename)
                else:
                    self.essence_data[master_mob.name][slot.slot_id] = self.get_linked_essence(source_mob)

    def get_essence_file(self, mob_name, slot_id):
        try:
            return self.essence_data[mob_name][slot_id]
        except Exception:
            log("Cannot find essence for %s slot %d" % (mob_name, slot_id), WARNING)
            return ""

    def get_embedded_essence_count(self):
        count = 0
        for master_mob in self.aaf.content.mastermobs():
            for slot in master_mob.slots:
                if isinstance(slot.segment, aaf2.components.Sequence):
                    source_mob = None
                    for component in slot.segment.components:
                        if isinstance(component, aaf2.components.SourceClip):
                            source_mob = component.mob
                            break
                    else:
                        continue
                elif isinstance(slot.segment, aaf2.components.SourceClip):
                    source_mob = slot.segment.mob
                if slot.segment.media_kind == "Sound" and source_mob.essence:
                    count += 1
        return count


    # Instead of using per-item volume curves (aka take volume envelope),
    # we collect data from items and "render" it to the track volume envelope.
    def collect_vol_pan_automation(self, track):
        envelopes = {
            "volume_envelope": [],
            "panning_envelope": []
        }
        for envelope in envelopes:
            for item in track["items"]:
                if envelope in item:
                    for point in item[envelope]:
                        envelopes[envelope].append({
                            "time": item["position"] + point["time"],
                            "value": point["value"]
                        })
                    del item[envelope]
                else:
                    if not envelopes[envelope]: continue
                    # We don't want items without automation to be affected
                    # by automation added by other items
                    envelopes[envelope].append({
                        "time": item["position"],
                        "value": 1.0
                    })
                    envelopes[envelope].append({
                        "time": item["position"] + item["duration"],
                        "value": 1.0
                    })

        # Add only if not empty
        if envelopes["volume_envelope"]:
            track["volume_envelope"] = envelopes["volume_envelope"]
        if envelopes["panning_envelope"]:
            track["panning_envelope"] = envelopes["panning_envelope"]

        return track

    # Function is meant to be called recursively.
    # It is supposed to gather whatever information it can and pass it to
    # its caller, who will append the new data to its own.
    # The topmost caller sets "position" and "duration", as well as fades,
    def parse_operation_group(self, group, edit_rate):

        item = {}

        # We could base volume envelope extraction on either group.operation.name
        # or group.parameters[].name depending on which is more prone to be constant.
        # For now both conditions have to be met, which may cause some automation to
        # be ignored if other software picks different operation or parameter names.
        if group.operation.name in ["Mono Audio Gain", "Audio Gain"]:
            for p in group.parameters:
                if p.name not in ["Amplitude", "Amplitude multiplier", "Level"]: continue
                if isinstance(p, aaf2.misc.VaryingValue):
                    item["volume_envelope"] = self.get_point_list(p, group.length / edit_rate)
                elif isinstance(p, aaf2.misc.ConstantValue):
                    item["volume"] = self.aafrational_value(p.value)

        if group.operation.name == "Mono Audio Pan":
            for p in group.parameters:
                points = self.get_point_list(p, group.length / edit_rate)
                if p.name == "Pan value":
                    item["panning_envelope"] = [{
                        "time": point["time"],
                        "value": point["value"] * -2 + 1
                    } for point in points]

        if group.operation.name == "Audio Effect":
            for p in group.parameters:
                if p.name == "":
                    # Vegas/MC saves per-item volume and panning automation
                    # but I haven't figured out a way to find out which is which
                    # since the parameter name is blank.
                    pass
                if p.name == "SpeedRatio":
                    item["playbackrate"] = self.aafrational_value(p.value)

        segment = group.segments[0]

        # Aaaargh, why is this a thing?
        if isinstance(segment, aaf2.components.Sequence):
            segment = segment.components[0]

        if isinstance(segment, aaf2.components.OperationGroup):
            item.update(self.parse_operation_group(segment, edit_rate))
        elif isinstance(segment, aaf2.components.SourceClip):
            item.update({
                "source": self.get_essence_file(segment.mob.name, segment.slot_id),
                "offset": segment.start / edit_rate,
            })

        return item

    def parse_sequence(self, sequence, edit_rate):
        items = []
        time = 0.0
        fade = 0  # 0 = no fade, 1 = fade, -1 = last component was filler
        fade_length = None
        fade_type = 0  # 0 = linear, 1 = power

        for component in sequence.components:
            try:
                duration = component.length / edit_rate

                if isinstance(component, aaf2.components.SourceClip):
                    item = {
                        "source": self.get_essence_file(component.mob.name, component.slot_id),
                        "offset": component.start / edit_rate,
                        "position": time,
                        "duration": duration,
                    }
                    if fade == 1:
                        item["fadein"] = fade_length
                        item["fadeintype"] = fade_type
                    fade = 0
                    items.append(item)
                    time += duration

                elif isinstance(component, aaf2.components.OperationGroup):
                    item = {
                        "position": time,
                        "duration": duration
                    }
                    item.update(self.parse_operation_group(component, edit_rate))
                    if fade == 1:
                        item["fadein"] = fade_length
                        item["fadeintype"] = fade_type
                    fade = 0

                    if "source" not in item:
                        log("Failed to find item source at %f seconds." % time, WARNING)
                        item["source"] = ""
                    if "offset" not in item:
                        log("Failed to find item offset at %f seconds." % time, WARNING)
                        item["offset"] = 0

                    items.append(item)
                    time += duration

                elif isinstance(component, aaf2.components.Transition):
                    fade_length = duration
                    fade_type = 0
                    try:
                        if component["OperationGroup"].value.parameters.value[0].interpolation.name == "PowerInterp":
                            fade_type = 1
                    except Exception:
                        pass
                    if fade == 0:
                        items[-1]["fadeout"] = fade_length
                        items[-1]["fadeouttype"] = fade_type
                    if fade != 1:
                        fade = 1
                    time -= duration

                elif isinstance(component, aaf2.components.Filler):
                    fade = -1
                    time += duration

            except Exception:
                log("Failed to parse component at %f seconds." % time)

        return items

    def get_picture_tracks(self, slot):
        data = []
        edit_rate = self.aafrational_value(slot.edit_rate)

        if isinstance(slot.segment, aaf2.components.NestedScope):
            for sequence in slot.segment.slots.value:
                seq_data = self.parse_sequence(sequence, edit_rate)
                if seq_data:
                    data.append({
                        "name": "",
                        "items": seq_data
                    })
        elif isinstance(slot.segment, aaf2.components.Sequence):
            seq_data = self.parse_sequence(slot.segment, edit_rate)
            if seq_data:
                data.append({
                    "name": slot.name,
                    "items": seq_data
                })

        return data

    def get_sound_track(self, slot):
        data = {
            "name": slot.name
        }
        edit_rate = self.aafrational_value(slot.edit_rate)
        segment = slot.segment
        if isinstance(segment, aaf2.components.OperationGroup):
            # Maybe we should check for segment.operation.name as well?
            for p in segment.parameters:
                if p.name == "Pan value":
                    data["panning"] = self.aafrational_value(p.value) * 2 - 1
                if p.name in ["Pan", "Pan Level"]:
                    # Sometimes segment.length is wrong so we have to use
                    # the length of the data segment instead.
                    real_length = segment.length / edit_rate
                    if self.encoder == "DaVinci Resolve":
                        real_length = segment.segments[0].length / edit_rate
                    points = self.get_point_list(p, real_length)
                    data["panning_envelope"] = [{
                        "time": point["time"],
                        "value": point["value"] * -2 + 1
                        # Reaper can't make up its mind 
                    } for point in points]
            data["items"] = self.parse_sequence(segment.segments[0], edit_rate)
        elif isinstance(segment, aaf2.components.Sequence):
            data["items"] = self.parse_sequence(segment, edit_rate)
        return data

    def get_markers(self, slot):
        markers = []
        edit_rate = self.aafrational_value(slot.edit_rate)
        for component in slot.segment.components:
            marker = {
                "name": component["Comment"].value,
                "position": component["Position"].value / edit_rate
            }
            if "CommentMarkerColour" in component:
                col = component["CommentMarkerColour"].value
                marker["colour"] = {
                    "r": int(col["red"] / 256),
                    "g": int(col["green"] / 256),
                    "b": int(col["blue"] / 256)
                }
            markers.append(marker)
        return markers

    def get_composition_list(self):
        return [composition.name for composition in self.aaf.content.compositionmobs()]

    def get_composition(self, composition):
        data = {
            "tracks": [],
            "markers": []
        }

        for slot in list(self.aaf.content.compositionmobs())[composition].slots:
            try:
                if slot.media_kind == "Picture":
                    picture_tracks = self.get_picture_tracks(slot)
                    if picture_tracks:
                        data["tracks"] += picture_tracks
                elif slot.media_kind in ["Sound", "LegacySound"]:
                    track_data = self.get_sound_track(slot)
                    track_data = self.collect_vol_pan_automation(track_data)
                    data["tracks"].append(track_data)
                elif slot.media_kind == "DescriptiveMetadata":
                    data["markers"] += self.get_markers(slot)
            except Exception:
                log("Failed parsing slot %s" % slot.name, WARNING)
        return data

    def get_aaf_metadata(self):
        try:
            identity = self.aaf.header["IdentificationList"][0]
            return {
                "company": identity["CompanyName"].value,
                "product": identity["ProductName"].value,
                "version": identity["ProductVersionString"].value,
                "date": identity["Date"].value,
                "platform": identity["Platform"].value
            }
        except Exception:
            warn("Could not get file identity metadata.", WARNING)
            return {}



class UserInteraction:

    @staticmethod
    def show_progressbar(item_count, action):

        def update_call(message):
            if len(message) > 50:
                message = message[:48] + "..."
            try:
                label.config(text=message)
                progressbar.step()
                progressbar.update()
            except Exception:
                # User closed the window, probably
                pass

        window = tkinter.Tk()
        window.title("Importing...")
        window.columnconfigure(0, weight=1)

        frame = tkinter.Frame(window, borderwidth=10)
        frame.grid(column=0, row=0, sticky="NWSE")
        frame.columnconfigure(0, weight=1)

        label = tkinter.Label(frame, text="")
        label.grid(column=0, row=0, sticky="NW")

        progressbar = tkinter.ttk.Progressbar(frame, mode="determinate", maximum=item_count+1, length=500)
        progressbar.grid(column=0, row=1, sticky="WE")

        action(update_call)
        try:
            window.destroy()
            window.mainloop()
        except Exception:
            pass

    @staticmethod
    def get_composition(composition_list):
        if have_reaper:
            if have_tk:
                return UserInteraction.get_composition_gui(composition_list)
            else:
                return UserInteraction.get_composition_awkward(composition_list)
        else:
            return UserInteraction.get_composition_cli(composition_list)

    @staticmethod
    def get_composition_cli(composition_list):
        print("Select composition to parse:")
        for i, t in enumerate(composition_list):
            print("%d. %s" % (i, t))
        while True:
            try:
                composition_id = int(input("> "))
                composition_list[composition_id]
                break
            except Exception:
                print("Invalid input.")
        return composition_id

    @staticmethod
    def get_composition_gui(composition_list):

        selection = 0

        def ok_callback():
            nonlocal selection
            selection = listbox.curselection()[0]
            window.destroy()

        def doubleclick_callback(e):
            ok_callback()

        window = tkinter.Tk()
        window.title("Select composition")
        window.rowconfigure(0, weight=1)
        window.columnconfigure(0, weight=1)

        frame = tkinter.Frame(window, borderwidth=10)
        frame.grid(column=0, row=0, sticky="NWSE")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        label_text = "AAF contains multiple compositions. Select which one to import:"
        label = tkinter.Label(frame, text=label_text)
        label.grid(column=0, row=0, sticky="NW")

        listbox = tkinter.Listbox(frame)
        for comp in composition_list:
            listbox.insert("end", comp)
        listbox.selection_set(0)
        listbox.see(0)
        listbox.activate(0)
        listbox.bind('<Double-1>', doubleclick_callback)
        listbox.grid(column=0, row=1, sticky="NWSE", pady=10)

        button = tkinter.Button(frame, text="OK", command=ok_callback)
        button.grid(column=0, row=2, sticky="S")

        window.mainloop()
        return selection

    @staticmethod
    def get_composition_awkward(composition_list):
        while True:
            for i, comp in enumerate(composition_list):
                message = "Do you want to import composition %s?" % comp
                result = RPR_MB(message, "Select composition", 4)
                if result == 6:
                    return i

def import_aaf():
    global log_level

    aaf_interface = AAFInterface()
    reaper_interface = ReaperInterface()

    if have_reaper:
        filename = reaper_interface.select_aaf()
        if filename is None: return
        target = reaper_interface.get_project_directory()
    else:
        if len(sys.argv) < 2:
            log("No input file provided.", ERROR)
            return
        filename = sys.argv[1]
        target = "sources"
        if not os.path.exists(target):
            os.mkdir(target)
        log_level = NOTICE

    if not aaf_interface.open(filename): return
    log("geting data from %s..." % filename)
    meta = aaf_interface.get_aaf_metadata()
    log("AAF created on %s with %s %s version %s using %s" % 
        (str(meta["date"]), meta["company"], meta["product"], meta["version"], meta["platform"])
    )

    if have_tk:
        def action(update):
            aaf_interface.extract_essence(target, update)
        count = aaf_interface.get_embedded_essence_count()
        UserInteraction.show_progressbar(count, action)
    else:
        aaf_interface.extract_essence(target, None)

    composition_list = aaf_interface.get_composition_list()
    composition_id = 0
    if len(composition_list) > 1:
        composition_id = UserInteraction.get_composition(composition_list)
    composition = aaf_interface.get_composition(composition_id)

    if have_reaper:
        reaper_interface.build_project(composition)
    else:
        print(json.dumps(composition))

if __name__ == "__main__":
    # sys.exit() or exit() would crash the script, so instead
    # we're using `return` within a main function
    import_aaf()

