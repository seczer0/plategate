# Plategate: Grab them by the plate!
Proof of Concept script described in the article [Plategate: Grab them by the plate!](https://www.linkedin.com/pulse/plategate-grab-them-plate-thomas-m%C3%BCller)

#### Usage
The script `grab-them-by-the-plate.py` queries the vehicle owner data for a range of swiss number plates. The number
 plates for the following cantons of Switzerland are supported:
* Aargau (AG)
* Lucerne (LU)
* Schaffhausen (SH)
* Zug (ZG)
* Zurich (ZH)

By default the owner data is written to the file `results.txt` in the current directory. 
```
usage: grab-them-by-the-plate.py [-h] [-t THREADS] [-o OUTFILE]
                                 {AG,LU,SH,ZG,ZH} start [end]

positional arguments:
  {AG,LU,SH,ZG,ZH}      the canton of the plate
  start                 the plate number to start with
  end                   the last plate number to query

optional arguments:
  -h, --help            show this help message and exit
  -t THREADS, --threads THREADS
                        number of threads to use for querying
  -o OUTFILE, --outfile OUTFILE
                        file where the results are written
```
##### Examples
* Query owner data for Zurich number plate ZH 42:\
`$ python grab-them-by-the-plate.py ZH 42`
* Query owner data for Lucerne number plates LU 23 - LU 42:\
`$ python grab-them-by-the-plate.py LU 23 42`
* Query owner data for Aargau number plates AG 23 - AG 42 using 5 threads for querying:\
`$ python grab-them-by-the-plate.py -t 5 AG 23 42`
* Query owner data for Schaffhausen number plates SH 23 - SH 42 using 5 threads for querying and store the results in `owner_sh23-42.txt`:\
`$ python grab-them-by-the-plate.py -t 5 -o owner_sh23-42.txt SH 23 42`

#### System Requirements
The script requires the imported python libraries and [tesseract](https://github.com/tesseract-ocr/tesseract) on the path.