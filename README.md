### Wagtail for PLOS Content

to run:

```console
pip install -r requirements.txt
python manage.py migrate
```

For creating a superuser
`python manage.py createsuperuser`

and to run

`python manage.py runserver`

### Exisiting CMS structure

#### Homepages Authoring CMS
Production homepage content is hosted at `journals.plos.org/{journal_slug}`, i.e. [PLOS One](https://journals.plos.org/plosone/).


#### Sitecontent Authoring CMS
Similarly, ancillary journal information is hosted at `/{journal_slug}/s/{article_slug}`, i.e.
[PLOS One data availability policy](https://journals.plos.org/plosone/s/data-availability). Note that other journals share similar content [PLOS Water data availability policy](https://journals.plos.org/water/s/data-availability).


#### Article Admin volumes and issues CMS
Volumes and Issues (which only certain journals use) can be viewed at `/{journal_slug}/volume` i.e [PLOS Medicine volumes](https://journals.plos.org/plosmedicine/volume) and an example issue would be [PLOS Medicinge March 2021](https://journals.plos.org/plosmedicine/issue?id=10.1371/issue.pmed.v18.i03)
