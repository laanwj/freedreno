#! /bin/sh
 python ../dmesg2rd.py $1.log $1.rd
../cffdump --no-color $1.rd > $1.disass
