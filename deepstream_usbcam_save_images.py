#!/usr/bin/env python3

################################################################################
# Copyright (c) 2020, NVIDIA CORPORATION. All rights reserved.
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the "Software"),
# to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.  IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.
################################################################################

import sys
sys.path.append('../')
import gi
import configparser
gi.require_version('Gst', '1.0')
from gi.repository import GObject, Gst
from gi.repository import GLib
from ctypes import *
import time
import sys
import numpy as np
import cv2
import math
import platform
from common.is_aarch_64 import is_aarch64
from common.bus_call import bus_call
from common.FPS import GETFPS
import pyds
from threading import Thread
from common.utils import long_to_int
import datetime
import os
import json
import time
from queue import Queue


MUXER_BATCH_TIMEOUT_USEC=4000000
TILED_OUTPUT_WIDTH=640*2
TILED_OUTPUT_HEIGHT=480
GST_CAPS_FEATURES_NVMM="memory:NVMM"
OSD_PROCESS_MODE= 0
OSD_DISPLAY_TEXT= 0

#start timer
init = time.time()

#create image directories
path1 = "positive"
path2 =  "negative"
if not os.path.exists(path1):
    os.mkdir(path1)
if not os.path.exists(path2):
    os.mkdir(path2)

#read config
try:
    with open("config.json", "r") as f:
        config = json.load(f)
        print(config)    

    if len(list(config)) == 0:
        print("No configurations provided in json file")
        sys.exit(1)

    display = config["display"]
    if not isinstance(display, bool):
        print("wrong value for 'display' in json file. Valid usage is 'display': true or 'display': false")
        sys.exit(1)
    MUXER_OUTPUT_WIDTH = config["processing_width"]
    if type(MUXER_OUTPUT_WIDTH)!=type(1):
        print("wrong value for 'processing_width' in json file. Should be integer. eg. 640")
        sys.exit(1)
    MUXER_OUTPUT_HEIGHT = config["processing_height"]
    if type(MUXER_OUTPUT_HEIGHT) != type(1):
        print("wrong value for 'processing_height' in json file. Should be integer. eg. 480")
        sys.exit(1)
    image_timer = config["image_timer"]
    if type(image_timer) != type(1):
        print("wrong value for 'image_timer' in json file. Should be integer. eg. 600")
        sys.exit(1)
    queue_size = config["queue_size"]
    if type(queue_size) != type(1):
        print("wrong value for 'queue_size' in json file. Should be integer and greater than 0. e.g. 20")
        sys.exit(1)
    else:
        if queue_size == 0:
            print("'queue_size' cannot be 0. Switching to default value 20.")
            queue_size = 20
            time.sleep(5)

except Exception as e:
    print(e)
    print("Error in json file")
    sys.exit(1)

number_sources = 1
id_dict = {}
fps_streams={}
for i in range(number_sources):
    #initialise id dictionary to keep track of object_id streamwise
    id_dict[i] = Queue(maxsize=queue_size)
    fps_streams["stream{0}".format(i)]=GETFPS(i)
    #create image directories for separate streams
    if not os.path.exists(os.path.join(path1,"stream_"+str(i))):
        os.mkdir(os.path.join(path1,"stream_"+str(i)))
    if not os.path.exists(os.path.join(path2,"stream_"+str(i))):
        os.mkdir(os.path.join(path2,"stream_"+str(i)))

# tiler_sink_pad_buffer_probe  will extract metadata received on OSD sink pad
# and update params for drawing rectangle, object information etc.
def tiler_src_pad_buffer_probe(pad,info,u_data):

    global init

    frame_number=0
    num_rects=0
    gst_buffer = info.get_buffer()
    if not gst_buffer:
        print("Unable to get GstBuffer ")
        return

    # Retrieve batch metadata from the gst_buffer
    # Note that pyds.gst_buffer_get_nvds_batch_meta() expects the
    # C address of gst_buffer as input, which is obtained with hash(gst_buffer)
    batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
    l_frame = batch_meta.frame_meta_list
    while l_frame is not None:
        try:
            # Note that l_frame.data needs a cast to pyds.NvDsFrameMeta
            # The casting is done by pyds.NvDsFrameMeta.cast()
            # The casting also keeps ownership of the underlying memory
            # in the C code, so the Python garbage collector will leave
            # it alone.
            frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
        except StopIteration:
            break

        '''
        print("Frame Number is ", frame_meta.frame_num)
        print("Source id is ", frame_meta.source_id)
        print("Batch id is ", frame_meta.batch_id)
        print("Source Frame Width ", frame_meta.source_frame_width)
        print("Source Frame Height ", frame_meta.source_frame_height)
        print("Num object meta ", frame_meta.num_obj_meta)
        '''
        frame_number=frame_meta.frame_num
        l_obj=frame_meta.obj_meta_list
        num_rects = frame_meta.num_obj_meta

        while l_obj is not None:
            try: 
                # Casting l_obj.data to pyds.NvDsObjectMeta
                obj_meta=pyds.NvDsObjectMeta.cast(l_obj.data)
            except StopIteration:
                break
            
            l_user_meta = obj_meta.obj_user_meta_list
            while l_user_meta:
                try:
                    user_meta = pyds.NvDsUserMeta.cast(l_user_meta.data)
                    if user_meta.base_meta.meta_type == pyds.nvds_get_user_meta_type("NVIDIA.DSANALYTICSOBJ.USER_META"):             
                        user_meta_data = pyds.NvDsAnalyticsObjInfo.cast(user_meta.user_meta_data)
                        # print("Object {0} line crossing status: {1}".format(obj_meta.object_id, user_meta_data.lcStatus))
                        print("Object {0} roi status: {1}".format(obj_meta.object_id, user_meta_data.roiStatus))
                        # if user_meta_data.dirStatus: print("Object {0} moving in direction: {1}".format(obj_meta.object_id, user_meta_data.dirStatus))                    
                        # if user_meta_data.lcStatus: print("Object {0} line crossing status: {1}".format(obj_meta.object_id, user_meta_data.lcStatus))
                        # if user_meta_data.ocStatus: print("Object {0} overcrowding status: {1}".format(obj_meta.object_id, user_meta_data.ocStatus))
                        # if user_meta_data.roiStatus: print("Object {0} roi status: {1}".format(obj_meta.object_id, user_meta_data.roiStatus))
                
                        if user_meta_data.roiStatus:
                            if obj_meta.object_id not in list(id_dict[frame_meta.pad_index].queue):
                                if id_dict[frame_meta.pad_index].full():
                                    id_dict[frame_meta.pad_index].get()
                                id_dict[frame_meta.pad_index].put(obj_meta.object_id)
                                #write image when object detected in "positive" folder
                                try:
                                    frame = get_frame(gst_buffer, frame_meta.batch_id)
                                    name= "img_"+datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")+".jpg"
                                    cv2.imwrite(os.path.join(os.path.join(path1,"stream_"+str(frame_meta.pad_index)), name), frame)
                                except cv2.error as e:
                                    print(e)
                
                except StopIteration:
                    break

                try:
                    l_user_meta = l_user_meta.next
                except StopIteration:
                    break

            try: 
                l_obj=l_obj.next
            except StopIteration:
                break            
        
        #write image every n secs if object not detected in "negative" folder
        if time.time() - init > image_timer:
            if frame_meta.num_obj_meta==0:
                try:
                    frame = get_frame(gst_buffer, frame_meta.batch_id)
                    name= "img_"+datetime.datetime.now().strftime("%Y%m%d_%H%M%S")+".jpg"
                    cv2.imwrite(os.path.join(os.path.join(path2,"stream_"+str(frame_meta.pad_index)), name), frame)
                    init = time.time()
                except cv2.error as e:
                    print(e)

        # Get frame rate through this probe
        fps_streams["stream{0}".format(frame_meta.pad_index)].get_fps()
        # print([list(id_dict[x].queue) for x in list(id_dict)])
        
        try:
            l_frame=l_frame.next
        except StopIteration:
            break
        
    return Gst.PadProbeReturn.OK

def get_frame(gst_buffer, batch_id):
    n_frame=pyds.get_nvds_buf_surface(hash(gst_buffer),batch_id)
    #convert python array into numy array format.
    frame_image=np.array(n_frame,copy=True,order='C')
    #covert the array into cv2 default color format
    frame_image=cv2.cvtColor(frame_image,cv2.COLOR_RGBA2BGRA)
    return frame_image

def main(args):

    # Standard GStreamer initialization
    GObject.threads_init()
    Gst.init(None)

    # Create gstreamer elements */
    # Create Pipeline element that will form a connection of other elements
    print("Creating Pipeline \n ")
    pipeline = Gst.Pipeline()

    if not pipeline:
        sys.stderr.write(" Unable to create Pipeline \n")
    print("Creating streamux \n ")

    # Source element for reading from the file
    print("Creating Source \n ")
    source = Gst.ElementFactory.make("v4l2src", "usb-cam-source")
    if not source:
        sys.stderr.write(" Unable to create Source \n")

    caps_v4l2src = Gst.ElementFactory.make("capsfilter", "v4l2src_caps")
    if not caps_v4l2src:
        sys.stderr.write(" Unable to create v4l2src capsfilter \n")

    print("Creating Video Converter \n")
    # videoconvert to make sure a superset of raw formats are supported
    vidconvsrc = Gst.ElementFactory.make("videoconvert", "convertor_src1")
    if not vidconvsrc:
        sys.stderr.write(" Unable to create videoconvert \n")

    # nvvideoconvert to convert incoming raw buffers to NVMM Mem (NvBufSurface API)
    nvvidconvsrc = Gst.ElementFactory.make("nvvideoconvert", "convertor_src2")
    if not nvvidconvsrc:
        sys.stderr.write(" Unable to create Nvvideoconvert \n")

    caps_vidconvsrc = Gst.ElementFactory.make("capsfilter", "nvmm_caps")
    if not caps_vidconvsrc:
        sys.stderr.write(" Unable to create capsfilter \n")

    # Create nvstreammux instance to form batches from one or more sources.
    streammux = Gst.ElementFactory.make("nvstreammux", "Stream-muxer")
    if not streammux:
        sys.stderr.write(" Unable to create NvStreamMux \n")
    
    print("Creating Pgie \n ")
    pgie = Gst.ElementFactory.make("nvinfer", "primary-inference")
    if not pgie:
        sys.stderr.write(" Unable to create pgie \n")

    tracker = Gst.ElementFactory.make("nvtracker", "tracker")
    if not tracker:
        sys.stderr.write(" Unable to create tracker \n")

    print("Creating nvdsanalytics \n ")
    nvanalytics = Gst.ElementFactory.make("nvdsanalytics", "analytics")
    if not nvanalytics:
        sys.stderr.write(" Unable to create nvanalytics \n")

    print("Creating tiler \n ")
    tiler=Gst.ElementFactory.make("nvmultistreamtiler", "nvtiler")
    if not tiler:
        sys.stderr.write(" Unable to create tiler \n")

    print("Creating nvvidconv1 \n ")
    nvvidconv1 = Gst.ElementFactory.make("nvvideoconvert", "convertor1")
    if not nvvidconv1:
        sys.stderr.write(" Unable to create nvvidconv1 \n")

    print("Creating filter1 \n ")
    caps1 = Gst.Caps.from_string("video/x-raw(memory:NVMM), format=RGBA")
    filter1 = Gst.ElementFactory.make("capsfilter", "filter1")
    if not filter1:
        sys.stderr.write(" Unable to get the caps filter1 \n")
    filter1.set_property("caps", caps1)

    print("Creating nvvidconv \n ")
    nvvidconv = Gst.ElementFactory.make("nvvideoconvert", "convertor")
    if not nvvidconv:
        sys.stderr.write(" Unable to create nvvidconv \n")

    print("Creating nvosd \n ")
    nvosd = Gst.ElementFactory.make("nvdsosd", "onscreendisplay")
    if not nvosd:
        sys.stderr.write(" Unable to create nvosd \n")

    # nvosd.set_property('process-mode',OSD_PROCESS_MODE)
    # nvosd.set_property('display-text',OSD_DISPLAY_TEXT)

    if(is_aarch64()):
        print("Creating transform \n ")
        transform=Gst.ElementFactory.make("nvegltransform", "nvegl-transform")
        if not transform:
            sys.stderr.write(" Unable to create transform \n")

    if display:
        print("Creating EGLSink \n")
        sink = Gst.ElementFactory.make("nveglglessink", "nvvideo-renderer")
        if not sink:
            sys.stderr.write(" Unable to create egl sink \n")
    else:
        print("Creating FakeSink \n")
        sink = Gst.ElementFactory.make("fakesink", "fakesink")
        if not sink:
            sys.stderr.write(" Unable to create fake sink \n")

    queue1=Gst.ElementFactory.make("queue","queue1")
    queue2=Gst.ElementFactory.make("queue","queue2")
    queue3=Gst.ElementFactory.make("queue","queue3")
    queue4=Gst.ElementFactory.make("queue","queue4")
    queue5=Gst.ElementFactory.make("queue","queue5")
    queue6=Gst.ElementFactory.make("queue","queue6")
    queue7=Gst.ElementFactory.make("queue","queue7")
    queue8=Gst.ElementFactory.make("queue","queue8")
    queue9=Gst.ElementFactory.make("queue","queue9")
    pipeline.add(queue1)
    pipeline.add(queue2)
    pipeline.add(queue3)
    pipeline.add(queue4)
    pipeline.add(queue5)
    pipeline.add(queue6)
    pipeline.add(queue7)
    pipeline.add(queue8)
    pipeline.add(queue9)

    print("Playing usbcam")
    source.set_property('device', "/dev/video0")
    caps_v4l2src.set_property('caps', Gst.Caps.from_string("video/x-raw, framerate=30/1, YUY2"))
    caps_vidconvsrc.set_property('caps', Gst.Caps.from_string("video/x-raw(memory:NVMM)"))

    streammux.set_property('width', MUXER_OUTPUT_WIDTH)
    streammux.set_property('height', MUXER_OUTPUT_HEIGHT)
    streammux.set_property('batch-size', number_sources)
    streammux.set_property('batched-push-timeout', 4000000)

    pgie.set_property('config-file-path', "primary_config.txt")
    pgie_batch_size=pgie.get_property("batch-size")
    if(pgie_batch_size != number_sources):
        print("WARNING: Overriding infer-config batch-size",pgie_batch_size," with number of sources ", number_sources," \n")
        pgie.set_property("batch-size",number_sources)

    # Set properties of tracker
    config = configparser.ConfigParser()
    config.read('dstest2_tracker_config.txt')
    config.sections()

    for key in config['tracker']:
        if key == 'tracker-width':
            tracker_width = config.getint('tracker', key)
            tracker.set_property('tracker-width', tracker_width)
        if key == 'tracker-height':
            tracker_height = config.getint('tracker', key)
            tracker.set_property('tracker-height', tracker_height)
        if key == 'gpu-id':
            tracker_gpu_id = config.getint('tracker', key)
            tracker.set_property('gpu_id', tracker_gpu_id)
        if key == 'll-lib-file':
            tracker_ll_lib_file = config.get('tracker', key)
            tracker.set_property('ll-lib-file', tracker_ll_lib_file)
        if key == 'll-config-file':
            tracker_ll_config_file = config.get('tracker', key)
            tracker.set_property('ll-config-file', tracker_ll_config_file)
        if key == 'enable-batch-process':
            tracker_enable_batch_process = config.getint('tracker', key)
            tracker.set_property('enable_batch_process',
                                 tracker_enable_batch_process)

    nvanalytics.set_property("config-file", "config_nvdsanalytics.txt")

    tiler_rows=int(math.sqrt(number_sources))
    tiler_columns=int(math.ceil((1.0*number_sources)/tiler_rows))
    tiler.set_property("rows",tiler_rows)
    tiler.set_property("columns",tiler_columns)
    tiler.set_property("width", TILED_OUTPUT_WIDTH)
    tiler.set_property("height", TILED_OUTPUT_HEIGHT)
    
    sink.set_property("qos",0)
    sink.set_property("sync",0)

    if not is_aarch64():
        # Use CUDA unified memory in the pipeline so frames
        # can be easily accessed on CPU in Python.
        mem_type = int(pyds.NVBUF_MEM_CUDA_UNIFIED)
        streammux.set_property("nvbuf-memory-type", mem_type)
        nvvidconv.set_property("nvbuf-memory-type", mem_type)
        nvvidconv1.set_property("nvbuf-memory-type", mem_type)
        tiler.set_property("nvbuf-memory-type", mem_type)

    print("Adding elements to Pipeline \n")
    pipeline.add(source)
    pipeline.add(caps_v4l2src)
    pipeline.add(vidconvsrc)
    pipeline.add(nvvidconvsrc)
    pipeline.add(caps_vidconvsrc)
    pipeline.add(streammux)
    pipeline.add(pgie)
    pipeline.add(tracker)
    pipeline.add(nvanalytics)
    pipeline.add(tiler)
    pipeline.add(nvvidconv)
    pipeline.add(filter1)
    pipeline.add(nvvidconv1)
    pipeline.add(nvosd)
    if is_aarch64():
        pipeline.add(transform)
    pipeline.add(sink)

    print("Linking elements in the Pipeline \n")
    source.link(caps_v4l2src)
    caps_v4l2src.link(vidconvsrc)
    vidconvsrc.link(nvvidconvsrc)
    nvvidconvsrc.link(caps_vidconvsrc)

    sinkpad = streammux.get_request_pad("sink_0")
    if not sinkpad:
        sys.stderr.write(" Unable to get the sink pad of streammux \n")
    srcpad = caps_vidconvsrc.get_static_pad("src")
    if not srcpad:
        sys.stderr.write(" Unable to get source pad of caps_vidconvsrc \n")
    srcpad.link(sinkpad)

    streammux.link(queue1)
    queue1.link(pgie)
    pgie.link(queue2)
    queue2.link(tracker)
    tracker.link(queue3)
    queue3.link(nvanalytics)
    nvanalytics.link(queue4)
    queue4.link(nvvidconv1)
    nvvidconv1.link(queue5)
    queue5.link(filter1)
    filter1.link(queue6)
    queue6.link(tiler)
    tiler.link(queue7)
    queue7.link(nvvidconv)
    nvvidconv.link(queue8)
    queue8.link(nvosd)
    if is_aarch64() and display:
        nvosd.link(queue9)
        queue9.link(transform)
        transform.link(sink)
    else:
        nvosd.link(queue9)
        queue9.link(sink)   

    # create an event loop and feed gstreamer bus messages to it
    loop = GObject.MainLoop()
    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect ("message", bus_call, loop)

    tiler_src_pad=tiler.get_static_pad("sink")
    if not tiler_src_pad:
        sys.stderr.write(" Unable to get src pad \n")
    tiler_src_pad.add_probe(Gst.PadProbeType.BUFFER, tiler_src_pad_buffer_probe, 0)

    print("Starting pipeline \n")
    # start play back and listed to events		
    pipeline.set_state(Gst.State.PLAYING)
    try:
        loop.run()
    except:
        pass
    # cleanup

    print("Exiting app\n")
    pipeline.set_state(Gst.State.NULL)

if __name__ == '__main__':
    sys.exit(main(sys.argv))


