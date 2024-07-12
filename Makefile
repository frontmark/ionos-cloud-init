ifeq ($(OS),Windows_NT)
ppth = $(shell cygpath -a -m -l .)
else
ppth = $(shell pwd -P)
endif

name = cloud-init
img  = frontmark/$(name):latest

caps = --cap-drop ALL
sopt = --security-opt no-new-privileges
rofs = --read-only --tmpfs /tmp
vol  = --mount type=bind,source='$(ppth)/datacenters',target=/datacenters,readonly
env  = $(if $(DATACENTER),-e DATACENTER=$(DATACENTER),) $(if $(LOCATION),-e LOCATION=$(LOCATION),) $(if $(ACTION),-e ACTION=$(ACTION),-e ACTION=create) $(if $(SERVER),-e SERVER=$(SERVER),) $(if $(VOLUME),-e VOLUME=$(VOLUME),) $(if $(NIC),-e NIC=$(NIC),) $(if $(FIREWALLRULE),-e FIREWALLRULE=$(FIREWALLRULE),)
args = $(caps) $(sopt) $(rofs) $(vol) $(env)

.PHONY: all build run stop rm up down init reinit rerun
.DEFAULT_GOAL := all
all: build

build:
	docker build --pull -t $(img) .
run:
	docker run -it --rm $(args) --name $(name)1 --hostname $(name)1 $(img)
stop rm:
	-docker container $@ $(name)1

up: build run

down: stop rm

init: up

reinit: down init

rerun: down run
