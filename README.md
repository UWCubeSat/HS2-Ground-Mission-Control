# Ground Mission Control

This repository holds the source code for HuskySat-2's ground control software. It is built upon a fork of the quickstart repository for YAMCS. Once complete, the software should be configured to meet our mission requirements, interface with 3rd party ground station providers, and convert F Prime command and telemetry dictionaries into YAMCS-compatible mission databases. 

## Prerequisites

* Java 17+
* Linux x64/aarch64, macOS x64/aarch64, or Windows x64

A copy of Maven is also required, however this gets automatically downloaded an installed by using the `./mvnw` shell script as detailed below.


## Running Yamcs

Here are some commands to get things started:

Compile this project:

    ./mvnw compile

Start Yamcs on localhost:

    ./mvnw yamcs:run

Same as yamcs:run, but allows a debugger to attach at port 7896:

    ./mvnw yamcs:debug
    
Delete all generated outputs and start over:

    ./mvnw clean

This will also delete Yamcs data. Change the `dataDir` property in `yamcs.yaml` to another location on your file system if you don't want that.


## Telemetry

To start pushing CCSDS packets into Yamcs, run the included Python script:

    python simulator.py

This script will send packets at 1 Hz over UDP to Yamcs. There is enough test data to run for a full calendar day.

The packets are a bit artificial and include a mixture of HK and accessory data.


## Telecommanding

This project defines a few example CCSDS telecommands. They are sent to UDP port 10025. The simulator.py script listens to this port. Commands  have no side effects. The script will only count them.


## Bundling

Running through Maven is useful during development, but it is not recommended for production environments. Instead bundle up your Yamcs application in a tar.gz file:

    ./mvnw package
    
