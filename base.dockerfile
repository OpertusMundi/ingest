# vim: set syntax=dockerfile:

FROM continuumio/miniconda3:4.10.3

COPY conda-env.yml /environment.yml
RUN conda env create -n env1

# see https://pythonspeed.com/articles/conda-docker-image-size/ 
RUN conda install -q -y -c conda-forge conda-pack 
