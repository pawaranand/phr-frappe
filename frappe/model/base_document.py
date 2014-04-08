# Copyright (c) 2013, Web Notes Technologies Pvt. Ltd. and Contributors
# MIT License. See license.txt

from __future__ import unicode_literals
import frappe
from frappe import _, msgprint
from frappe.utils import cint, flt, cstr, now
from frappe.model import default_fields
from frappe.model.naming import set_new_name

class BaseDocument(object):
	ignore_in_getter = ("doctype", "_meta", "meta", "_table_fields", "_valid_columns")

	def __init__(self, d):
		self.update(d)

	@property
	def meta(self):
		if not hasattr(self, "_meta"):
			self._meta = frappe.get_meta(self.doctype)

		return self._meta

	def update(self, d):
		if "doctype" in d:
			self.set("doctype", d.get("doctype"))
		for key, value in d.iteritems():
			self.set(key, value)

	def update_if_missing(self, d):
		if isinstance(d, BaseDocument):
			d = d.get_valid_dict()

		if "doctype" in d:
			self.set("doctype", d.get("doctype"))
		for key, value in d.iteritems():
			if self.get(key) is None:
				self.set(key, value)

	def get_db_value(self, key):
		return frappe.db.get_value(self.doctype, self.name, key)

	def get(self, key=None, filters=None, limit=None, default=None):
		if key:
			if isinstance(key, dict):
				return _filter(self.get_all_children(), key, limit=limit)
			if filters:
				if isinstance(filters, dict):
					value = _filter(self.__dict__.get(key), filters, limit=limit)
				else:
					default = filters
					filters = None
					value = self.__dict__.get(key, default)
			else:
				value = self.__dict__.get(key, default)

			if value is None and key not in self.ignore_in_getter \
				and key in (d.fieldname for d in self.meta.get_table_fields()):
				self.set(key, [])
				value = self.__dict__.get(key)

			return value
		else:
			return self.__dict__

	def getone(self, key, filters=None):
		return self.get(key, filters=filters, limit=1)[0]

	def set(self, key, value):
		if isinstance(value, list):
			self.__dict__[key] = []
			self.extend(key, value)
		else:
			self.__dict__[key] = value

	def append(self, key, value=None):
		if value==None:
			value={}
		if isinstance(value, (dict, BaseDocument)):
			if not self.__dict__.get(key):
				self.__dict__[key] = []
			value = self._init_child(value, key)
			self.__dict__[key].append(value)
			return value
		else:
			raise ValueError

	def extend(self, key, value):
		if isinstance(value, list):
			for v in value:
				self.append(key, v)
		else:
			raise ValueError

	def remove(self, doc):
		self.get(doc.parentfield).remove(doc)

	def _init_child(self, value, key):
		if not self.doctype:
			return value
		if not isinstance(value, BaseDocument):
			if "doctype" not in value:
				value["doctype"] = self.get_table_field_doctype(key)
				if not value["doctype"]:
					raise AttributeError, key
			value = BaseDocument(value)
			value.init_valid_columns()

		value.parent = self.name
		value.parenttype = self.doctype
		value.parentfield = key
		if not getattr(value, "idx", None):
			value.idx = len(self.get(key) or []) + 1

		return value

	def get_valid_dict(self):
		d = {}
		for fieldname in self.meta.get_valid_columns():
			d[fieldname] = self.get(fieldname)
		return d

	def init_valid_columns(self):
		for key in default_fields:
			if key not in self.__dict__:
				self.__dict__[key] = None

		if self.doctype in ("DocField", "DocPerm") and self.parent in ("DocType", "DocField", "DocPerm"):
			from frappe.model.meta import get_table_columns
			valid = get_table_columns(self.doctype)
		else:
			valid = self.meta.get_valid_columns()

		for key in valid:
			if key not in self.__dict__:
				self.__dict__[key] = None

	def is_new(self):
		return self.get("__islocal")

	def as_dict(self):
		doc = self.get_valid_dict()
		doc["doctype"] = self.doctype
		for df in self.meta.get_table_fields():
			children = self.get(df.fieldname) or []
			doc[df.fieldname] = [d.as_dict() for d in children]
		return doc

	def get_table_field_doctype(self, fieldname):
		return self.meta.get_field(fieldname).options

	def get_parentfield_of_doctype(self, doctype):
		fieldname = [df.fieldname for df in self.meta.get_table_fields() if df.options==doctype]
		return fieldname[0] if fieldname else None

	def db_insert(self):
		set_new_name(self)
		d = self.get_valid_dict()
		columns = d.keys()
		frappe.db.sql("""insert into `tab{doctype}`
			({columns}) values ({values})""".format(
				doctype = self.doctype,
				columns = ", ".join(["`"+c+"`" for c in columns]),
				values = ", ".join(["%s"] * len(columns))
			), d.values())

		self.set("__islocal", False)

	def db_update(self):
		if self.get("__islocal") or not self.name:
			self.db_insert()
			return

		d = self.get_valid_dict()
		columns = d.keys()
		frappe.db.sql("""update `tab{doctype}`
			set {values} where name=%s""".format(
				doctype = self.doctype,
				values = ", ".join(["`"+c+"`=%s" for c in columns])
			), d.values() + [d.get("name")])

	def _fix_numeric_types(self):
		for df in self.meta.get("fields"):
			if df.fieldtype in ("Int", "Check"):
				self.set(df.fieldname, cint(self.get(df.fieldname)))
			elif df.fieldtype in ("Float", "Currency"):
				self.set(df.fieldname, flt(self.get(df.fieldname)))

		if self.docstatus is not None:
			self.docstatus = cint(self.docstatus)

	def _get_missing_mandatory_fields(self):
		"""Get mandatory fields that do not have any values"""
		def get_msg(df):
			if df.fieldtype == "Table":
				return "{}: {}: {}".format(_("Error"), _("Data missing in table"), _(df.label))

			elif self.parentfield:
				return "{}: {} #{}: {}: {}".format(_("Error"), _("Row"), self.idx,
					_("Value missing for"), _(df.label))

			else:
				return "{}: {}: {}".format(_("Error"), _("Value missing for"), _(df.label))

		missing = []

		for df in self.meta.get("fields", {"reqd": 1}):
			if self.get(df.fieldname) in (None, []):
				missing.append((df.fieldname, get_msg(df)))

		return missing

	def get_invalid_links(self):
		def get_msg(df, docname):
			if self.parentfield:
				return "{} #{}: {}: {}".format(_("Row"), self.idx, _(df.label), docname)
			else:
				return "{}: {}".format(_(df.label), docname)

		invalid_links = []
		for df in self.meta.get_link_fields():
			doctype = df.options

			if not doctype:
				frappe.throw("Options not set for link field: {}".format(df.fieldname))

			elif doctype.lower().startswith("link:"):
				doctype = doctype[5:]

			docname = self.get(df.fieldname)
			if docname and not frappe.db.get_value(doctype, docname):
				invalid_links.append((df.fieldname, docname, get_msg(df, docname)))

		return invalid_links

	def _validate_constants(self):
		if frappe.flags.in_import:
			return

		constants = [d.fieldname for d in self.meta.get("fields", {"set_only_once": 1})]
		if constants:
			values = frappe.db.get_value(self.doctype, self.name, constants, as_dict=True)

		for fieldname in constants:
			if self.get(fieldname) != values.get(fieldname):
				frappe.throw("{0}: {1}".format(_("Value cannot be changed for"),
					_(self.meta.get_label(fieldname))),
					frappe.CannotChangeConstantError)

def _filter(data, filters, limit=None):
	"""pass filters as:
		{"key": "val", "key": ["!=", "val"],
		"key": ["in", "val"], "key": ["not in", "val"], "key": "^val",
		"key" : True (exists), "key": False (does not exist) }"""

	out = []

	for d in data:
		add = True
		for f in filters:
			fval = filters[f]

			if fval is True:
				fval = ("not None", fval)
			elif fval is False:
				fval = ("None", fval)
			elif not isinstance(fval, (tuple, list)):
				if isinstance(fval, basestring) and fval.startswith("^"):
					fval = ("^", fval[1:])
				else:
					fval = ("=", fval)

			if not frappe.compare(getattr(d, f, None), fval[0], fval[1]):
				add = False
				break

		if add:
			out.append(d)
			if limit and (len(out)-1)==limit:
				break

	return out
